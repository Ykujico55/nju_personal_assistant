"""Versioned draft artifacts for the smail extension.

A draft version binds recipients, subject, body and the complete attachment
manifest (including every attachment hash).  Editing any field creates a new
version and a new local action id, so an approval created for an older version
can never match the new digest.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from personal_assistant_sdk import HostArtifactClient, HostCapabilityError, HostMailClient

from .mime import (
    build_message_bytes,
    canonical_draft_digest,
    sanitize_filename,
)
from .models import AccountConfig, ExtensionSettings, MailConfigError
from .store import MailStore

MAX_RECIPIENTS = 100
MAX_ATTACHMENTS = 16


class DraftService:
    def __init__(
        self,
        store: MailStore,
        settings: ExtensionSettings,
        artifacts: HostArtifactClient | None,
        host_mail: HostMailClient | None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._artifacts = artifacts
        self._host_mail = host_mail

    async def prepare(
        self, arguments: Mapping[str, Any], request_id: str | None = None
    ) -> dict[str, Any]:
        account = self._account(arguments)
        info = await self._account_info(account.account_id)
        draft_id = arguments.get("draft_id")
        if draft_id is not None and not isinstance(draft_id, str):
            raise MailConfigError("SMAL_DRAFT_INVALID", "draft_id must be a string")
        if not draft_id:
            # A creation replay (same at-least-once request) must land on the
            # same draft id; derive it from the request id when available.
            clean_request = (request_id or "").strip()
            if clean_request:
                draft_id = "draft_" + hashlib.sha256(clean_request.encode()).hexdigest()[:32]
            else:
                draft_id = f"draft_{uuid4().hex}"
        existing = await self._store.get_draft(draft_id)
        if existing is not None and existing["account_id"] != account.account_id:
            raise MailConfigError(
                "SMAL_DRAFT_INVALID", "the draft belongs to a different account"
            )
        thread_id = arguments.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            thread_id = existing.get("thread_id") if existing else None
        to = _recipients(arguments.get("to", []))
        cc = _recipients(arguments.get("cc", []))
        bcc = _recipients(arguments.get("bcc", []))
        if not to and not cc and not bcc:
            raise MailConfigError("SMAL_DRAFT_INVALID", "at least one recipient is required")
        subject = _text(arguments.get("subject", ""), maximum=998)
        body_text = _text(arguments.get("body_text", ""), maximum=262_144, allow_empty=True)
        body_html_raw = arguments.get("body_html")
        body_html = (
            _text(body_html_raw, maximum=524_288, allow_empty=True)
            if body_html_raw is not None
            else None
        )
        in_reply_to = arguments.get("in_reply_to")
        if in_reply_to is not None:
            in_reply_to = _text(in_reply_to, maximum=320)
        references = _references(arguments.get("references", []))
        attachments = await self._load_attachments(arguments.get("attachments", []))
        manifest = [
            {
                "filename": filename,
                "media_type": media_type,
                "sha256": digest,
                "size_bytes": len(data),
            }
            for filename, media_type, digest, data in attachments
        ]
        canonical_payload = {
            "account_id": account.account_id,
            "account_fingerprint": info.fingerprint,
            "thread_id": thread_id,
            "to": list(to),
            "cc": list(cc),
            "bcc": list(bcc),
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "in_reply_to": in_reply_to,
            "references": list(references),
            "attachments": manifest,
        }
        canonical_digest = canonical_draft_digest(canonical_payload)
        revision_request_id = (request_id or "").strip() or uuid4().hex
        if request_id:
            previous = await self._store.get_draft_version_by_request(
                draft_id, revision_request_id
            )
            if previous is not None:
                if previous["canonical_digest"] != canonical_digest:
                    raise MailConfigError(
                        "SMAL_IDEMPOTENCY_CONFLICT",
                        "the revision request is bound to different draft content",
                    )
                # Same at-least-once request replayed: return the original
                # version without creating a new artifact or action id.
                return self._version_output(previous, info)
        local_action_id = "act_" + hashlib.sha256(
            f"{draft_id}|{revision_request_id}|{canonical_digest}".encode()
        ).hexdigest()[:40]
        domain = info.address.partition("@")[2] or "localhost"
        message_id = f"<smail.{local_action_id}@{domain}>"
        mime_bytes = build_message_bytes(
            message_id=message_id,
            from_address=info.address,
            to_addresses=to,
            cc_addresses=cc,
            bcc_addresses=bcc,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            sent_at=datetime.now(UTC),
            in_reply_to=in_reply_to,
            references=references,
            attachments=[(name, media_type, data) for name, media_type, _, data in attachments],
        )
        mime_sha256 = hashlib.sha256(mime_bytes).hexdigest()
        handle = await self._put_artifact(mime_bytes)
        try:
            version = await self._store.insert_draft_version(
                draft_id=draft_id,
                account_id=account.account_id,
                account_fingerprint=info.fingerprint,
                from_address=info.address,
                thread_id=thread_id,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                attachment_manifest=manifest,
                canonical_digest=canonical_digest,
                mime_sha256=mime_sha256,
                mime_artifact_id=handle.id,
                local_action_id=local_action_id,
                revision_request_id=revision_request_id,
                message_id=message_id,
                in_reply_to=in_reply_to,
                references=references,
                expected_current=existing["current_version"] if existing else 0,
            )
        except BaseException:
            # A failed or cancelled prepare must not leave an orphan MIME
            # artifact; the shielded delete keeps running through repeated
            # cancellation before the original signal is re-raised.
            if self._artifacts is not None:
                await _delete_artifact_shielded(self._artifacts, handle.id)
            if request_id:
                # A racing duplicate of the same request may have inserted the
                # version (unique index); replay it if the content matches.
                with contextlib.suppress(Exception):
                    raced = await self._store.get_draft_version_by_request(
                        draft_id, revision_request_id
                    )
                    if raced is not None and raced["canonical_digest"] == canonical_digest:
                        return self._version_output(raced, info)
            raise
        return {
            "draft_id": draft_id,
            "version": version,
            "account_id": account.account_id,
            "account_fingerprint": info.fingerprint,
            "from_address": info.address,
            "canonical_digest": canonical_digest,
            "local_action_id": local_action_id,
            "message_id": message_id,
            "mime_sha256": mime_sha256,
            "mime_artifact_id": handle.id,
            "to": list(to),
            "cc": list(cc),
            "bcc": list(bcc),
            "subject": subject,
            "attachment_hashes": [item["sha256"] for item in manifest],
        }

    def _version_output(
        self, version: Mapping[str, Any], info: Any
    ) -> dict[str, Any]:
        manifest = version.get("attachment_manifest")
        if not isinstance(manifest, list):
            manifest = []
        return {
            "draft_id": str(version["draft_id"]),
            "version": int(version["version"]),
            "account_id": str(version["account_id"]),
            "account_fingerprint": str(version["account_fingerprint"]),
            "from_address": str(version["from_address"]),
            "canonical_digest": str(version["canonical_digest"]),
            "local_action_id": str(version["local_action_id"]),
            "message_id": str(version["message_id"]),
            "mime_sha256": str(version["mime_sha256"]),
            "mime_artifact_id": str(version["mime_artifact_id"]),
            "to": list(version["to_json"]),
            "cc": list(version["cc_json"]),
            "bcc": list(version["bcc_json"]),
            "subject": str(version["subject"]),
            "attachment_hashes": [str(item["sha256"]) for item in manifest],
        }

    def _account(self, arguments: Mapping[str, Any]) -> AccountConfig:
        account_id = arguments.get("account_id")
        if not isinstance(account_id, str) or not account_id:
            raise MailConfigError("SMAL_ACCOUNT_UNKNOWN", "account_id is required")
        return self._settings.account(account_id)

    async def _account_info(self, account_id: str) -> Any:
        if self._host_mail is None:
            raise MailConfigError(
                "SMAL_MAIL_UNAVAILABLE", "the mail capability is not available"
            )
        try:
            return await self._host_mail.account(account_id)
        except HostCapabilityError as exc:
            code = getattr(exc, "code", "MAIL_UNAVAILABLE")
            raise MailConfigError(
                str(code), "the host account registry rejected this account"
            ) from None

    async def _load_attachments(
        self, raw: Any
    ) -> list[tuple[str, str, str, bytes]]:
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > MAX_ATTACHMENTS:
            raise MailConfigError("SMAL_DRAFT_INVALID", "invalid attachment list")
        loaded: list[tuple[str, str, str, bytes]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise MailConfigError("SMAL_DRAFT_INVALID", "invalid attachment entry")
            filename = sanitize_filename(str(item.get("filename", "attachment")))
            media_type = str(item.get("media_type") or "application/octet-stream")
            expected = item.get("sha256")
            artifact_id = item.get("artifact_id")
            if not isinstance(artifact_id, str) or not artifact_id:
                raise MailConfigError(
                    "SMAL_ATTACHMENT_UNSUPPORTED",
                    "attachments must reference a managed artifact",
                )
            if self._artifacts is None:
                raise MailConfigError(
                    "SMAL_ATTACHMENT_UNAVAILABLE",
                    "the artifact capability is not available",
                )
            data = await self._artifacts.read(artifact_id)
            digest = hashlib.sha256(data).hexdigest()
            if isinstance(expected, str) and expected and expected != digest:
                raise MailConfigError(
                    "SMAL_ATTACHMENT_MISMATCH",
                    "the attachment hash does not match the artifact",
                )
            size = item.get("size_bytes")
            if isinstance(size, int) and not isinstance(size, bool) and size != len(data):
                raise MailConfigError(
                    "SMAL_ATTACHMENT_MISMATCH", "the attachment size does not match"
                )
            loaded.append((filename, media_type, digest, data))
        return loaded

    async def _put_artifact(self, mime_bytes: bytes) -> Any:
        if self._artifacts is None:
            raise MailConfigError(
                "SMAL_ARTIFACT_UNAVAILABLE", "the artifact capability is not available"
            )
        return await self._artifacts.put(
            mime_bytes, media_type="message/rfc822", sensitivity="PERSONAL"
        )


async def _delete_artifact_shielded(
    artifacts: HostArtifactClient, artifact_id: str
) -> None:
    """Delete a failed-draft artifact, surviving repeated cancellation."""

    task = asyncio.ensure_future(artifacts.delete(artifact_id))
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
            continue
        except Exception:  # noqa: BLE001 - best-effort cleanup
            break
    with contextlib.suppress(Exception, asyncio.CancelledError):
        task.exception()


def _recipients(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_RECIPIENTS:
        raise MailConfigError("SMAL_DRAFT_INVALID", "recipients must be a bounded list")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise MailConfigError("SMAL_DRAFT_INVALID", "recipient must be a string")
        address = item.strip().lower()
        if (
            not address
            or "@" not in address
            or len(address) > 320
            or any(character in address for character in " <>,;\r\n")
        ):
            raise MailConfigError("SMAL_DRAFT_INVALID", "recipient address is invalid")
        if address not in seen:
            seen.add(address)
            normalized.append(address)
    return tuple(normalized)


def _references(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 50:
        raise MailConfigError("SMAL_DRAFT_INVALID", "references must be a bounded list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 320 or "\r" in item or "\n" in item:
            raise MailConfigError("SMAL_DRAFT_INVALID", "reference is invalid")
        result.append(item)
    return tuple(result)


def _text(value: Any, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise MailConfigError("SMAL_DRAFT_INVALID", "expected a text field")
    if not value and not allow_empty:
        raise MailConfigError("SMAL_DRAFT_INVALID", "text field must not be empty")
    if len(value) > maximum:
        raise MailConfigError("SMAL_DRAFT_INVALID", "text field is too long")
    return value


__all__ = ["DraftService"]
