"""Send materialization, result recording and Sent-folder reconciliation.

The extension never submits SMTP: the host executor asks for the exact current
draft bytes (only when the requested version is still current), sends them
behind the Tool Gateway and then records the per-recipient transport result.
An UNKNOWN outcome is never retried automatically; it converges through the
read-only Sent folder or an explicit user decision.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from personal_assistant_sdk import HostCapabilityError, HostMailClient

from .mime import canonical_draft_digest
from .models import AccountConfig, ExtensionSettings, MailConfigError
from .store import MailStore

HOST_STATUSES = frozenset(
    {"PREPARED", "EXECUTING", "SUCCEEDED", "PARTIAL", "FAILED", "UNKNOWN"}
)
HOST_NON_TERMINAL = frozenset({"PREPARED", "EXECUTING"})


def canonical_sha256(value: Any) -> str:
    normalized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class SendService:
    def __init__(
        self,
        store: MailStore,
        settings: ExtensionSettings,
        host_mail: HostMailClient | None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._host_mail = host_mail

    async def materialize(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        account, draft_id, draft_version = await self._send_identity(arguments)
        draft = await self._store.get_draft(draft_id)
        if draft is None:
            raise MailConfigError("SMAL_DRAFT_UNKNOWN", "the draft does not exist")
        if int(draft["current_version"]) != draft_version:
            raise MailConfigError(
                "SMAL_DRAFT_CHANGED",
                "the draft was edited after this action was approved",
            )
        version = await self._store.get_draft_version(draft_id, draft_version)
        if version is None:
            raise MailConfigError("SMAL_DRAFT_UNKNOWN", "the draft version does not exist")
        self._verify(arguments, version)
        envelope_digest = canonical_sha256(dict(arguments))
        await self._store.prepare_action(
            local_action_id=_required_text(arguments, "local_action_id"),
            account_id=account.account_id,
            draft_id=draft_id,
            draft_version=draft_version,
            message_id=_required_text(arguments, "message_id"),
            envelope_digest=envelope_digest,
        )
        manifest = _manifest(version)
        return {
            "draft_id": draft_id,
            "draft_version": draft_version,
            "account_id": account.account_id,
            "account_fingerprint": str(version["account_fingerprint"]),
            "from_address": str(version["from_address"]),
            "local_action_id": str(version["local_action_id"]),
            "message_id": str(version["message_id"]),
            "mime_artifact_id": str(version["mime_artifact_id"]),
            "mime_sha256": str(version["mime_sha256"]),
            "to": list(version["to_json"]),
            "cc": list(version["cc_json"]),
            "bcc": list(version["bcc_json"]),
            "subject": str(version["subject"]),
            "attachment_hashes": [str(item["sha256"]) for item in manifest],
        }

    async def sync_status(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Project the host-authoritative ledger status into local state."""

        account_id = _required_text(arguments, "account_id")
        local_action_id = _required_text(arguments, "local_action_id")
        self._settings.account(account_id)
        if self._host_mail is None:
            raise MailConfigError(
                "SMAL_MAIL_UNAVAILABLE", "the mail capability is not available"
            )
        try:
            result = await self._host_mail.delivery_status(
                account_id, local_action_id=local_action_id
            )
        except HostCapabilityError as exc:
            code = getattr(exc, "code", "MAIL_UNAVAILABLE")
            raise MailConfigError(str(code), "the host ledger rejected this action") from None
        status = str(result.get("status") or "")
        if status not in HOST_STATUSES:
            raise MailConfigError("SMAL_SEND_STATE_INVALID", "the host returned an unknown status")
        action = await self._store.get_action(local_action_id)
        if action is None:
            raise MailConfigError("SMAL_ACTION_UNKNOWN", "the send action does not exist")
        if status in HOST_NON_TERMINAL:
            # The host is still PREPARED/EXECUTING: report the host status
            # without projecting a terminal state.
            return {
                "local_action_id": local_action_id,
                "state": str(action["state"]),
                "found": True,
                "authoritative_status": status,
            }
        projected = await self._store.project_status(
            local_action_id=local_action_id,
            state=status,
            receipt=dict(result),
            recipient_results=_result_list(result.get("recipient_results")),
        )
        return {
            "local_action_id": local_action_id,
            "state": str(projected["state"]) if projected is not None else status,
            "found": projected is not None,
            "authoritative_status": status,
        }

    async def reconcile(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        local_action_id = _required_text(arguments, "local_action_id")
        account_id = _required_text(arguments, "account_id")
        self._settings.account(account_id)
        action = await self._store.get_action(local_action_id)
        if action is None:
            raise MailConfigError("SMAL_ACTION_UNKNOWN", "the send action does not exist")
        if self._host_mail is None:
            raise MailConfigError(
                "SMAL_MAIL_UNAVAILABLE", "the mail read capability is not available"
            )
        sent_folder = arguments.get("sent_folder")
        folder = (
            sent_folder
            if isinstance(sent_folder, str) and sent_folder
            else "Sent"
        )
        try:
            result = await self._host_mail.reconcile_sent(
                account_id,
                local_action_id=local_action_id,
                message_id=str(action["message_id"]),
                sent_folder=folder,
            )
        except HostCapabilityError as exc:
            code = getattr(exc, "code", "MAIL_UNAVAILABLE")
            return {
                "local_action_id": local_action_id,
                "state": str(action["state"]),
                "reconciliation": "UNAVAILABLE",
                "diagnostic_code": str(code),
            }
        status = str(result.get("status", "UNAVAILABLE"))
        receipt: dict[str, Any] = {"reconciliation": status}
        projection: str | None = None
        if status == "MATCHED":
            projection = "SENT_CONFIRMED"
            receipt["matches"] = result.get("matches", [])
        elif status == "AMBIGUOUS":
            projection = "NEEDS_USER_ACTION"
            receipt["matches"] = result.get("matches", [])
        elif status == "NOT_FOUND":
            projection = "UNKNOWN"
            receipt["diagnostic_code"] = "RECONCILE_NOT_FOUND"
        projected: dict[str, Any] | None = action
        if projection is not None:
            projected = await self._store.project_status(
                local_action_id=local_action_id,
                state=projection,
                receipt=receipt,
                recipient_results=_results(action),
            )
        state = str(projected["state"]) if projected is not None else str(action["state"])
        return {
            "local_action_id": local_action_id,
            "state": state,
            "reconciliation": status,
            "diagnostic_code": result.get("diagnostic_code"),
        }

    # -- internals ----------------------------------------------------------

    async def _send_identity(
        self, arguments: Mapping[str, Any]
    ) -> tuple[AccountConfig, str, int]:
        account_id = _required_text(arguments, "account_id")
        account = self._settings.account(account_id)
        fingerprint = _required_text(arguments, "account_fingerprint")
        if self._host_mail is None:
            raise MailConfigError(
                "SMAL_MAIL_UNAVAILABLE", "the mail capability is not available"
            )
        try:
            info = await self._host_mail.account(account_id)
        except HostCapabilityError as exc:
            code = getattr(exc, "code", "MAIL_UNAVAILABLE")
            raise MailConfigError(
                str(code), "the host account registry rejected this account"
            ) from None
        if info.fingerprint != fingerprint:
            raise MailConfigError(
                "SMAL_ACCOUNT_CHANGED",
                "the account binding changed after this action was approved",
            )
        draft_id = _required_text(arguments, "draft_id")
        draft_version = arguments.get("draft_version")
        if (
            isinstance(draft_version, bool)
            or not isinstance(draft_version, int)
            or draft_version < 1
        ):
            raise MailConfigError("SMAL_DRAFT_INVALID", "draft_version is invalid")
        return account, draft_id, draft_version

    def _verify(
        self,
        arguments: Mapping[str, Any],
        version: Mapping[str, Any],
    ) -> None:
        if _required_text(arguments, "account_fingerprint") != str(
            version["account_fingerprint"]
        ):
            raise MailConfigError(
                "SMAL_SNAPSHOT_MISMATCH", "the account fingerprint does not match the draft"
            )
        if _required_text(arguments, "canonical_digest") != str(version["canonical_digest"]):
            raise MailConfigError(
                "SMAL_SNAPSHOT_MISMATCH", "the approved digest does not match the draft"
            )
        if _required_text(arguments, "local_action_id") != str(version["local_action_id"]):
            raise MailConfigError(
                "SMAL_SNAPSHOT_MISMATCH", "the local action id does not match the draft"
            )
        if _required_text(arguments, "message_id") != str(version["message_id"]):
            raise MailConfigError(
                "SMAL_SNAPSHOT_MISMATCH", "the message id does not match the draft"
            )
        if _required_text(arguments, "mime_sha256") != str(version["mime_sha256"]):
            raise MailConfigError(
                "SMAL_SNAPSHOT_MISMATCH", "the MIME hash does not match the draft"
            )
        from_address = _required_text(arguments, "from_address")
        if from_address.lower() != str(version["from_address"]).lower():
            raise MailConfigError("SMAL_SNAPSHOT_MISMATCH", "the sender does not match")
        if _required_text(arguments, "subject", allow_empty=True) != str(version["subject"]):
            raise MailConfigError("SMAL_SNAPSHOT_MISMATCH", "the subject does not match")
        for key, column in (("to", "to_json"), ("cc", "cc_json"), ("bcc", "bcc_json")):
            if _addresses(arguments.get(key, [])) != _addresses(version[column]):
                raise MailConfigError(
                    "SMAL_SNAPSHOT_MISMATCH", f"the {key} recipients do not match"
                )
        expected_hashes = sorted(str(item["sha256"]) for item in _manifest(version))
        supplied = arguments.get("attachment_hashes", [])
        if (
            not isinstance(supplied, list)
            or sorted(str(item) for item in supplied) != expected_hashes
        ):
            raise MailConfigError(
                "SMAL_SNAPSHOT_MISMATCH", "the attachment hashes do not match"
            )


def _manifest(version: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = version.get("attachment_manifest")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _result_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _results(action: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = action.get("recipient_results")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _addresses(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ()
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return ()
    return tuple(sorted(str(item).strip().lower() for item in value if isinstance(item, str)))


def _required_text(
    arguments: Mapping[str, Any], key: str, *, allow_empty: bool = False
) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or (not value and not allow_empty):
        raise MailConfigError("SMAL_SEND_INVALID", f"{key} is required")
    if len(value) > 4096 or "\r" in value or "\n" in value:
        raise MailConfigError("SMAL_SEND_INVALID", f"{key} is invalid")
    return value


# Kept for symmetry with the draft digest; the canonical JSON must match the
# host executor's `canonical_sha256` byte for byte.
def arguments_digest(arguments: Mapping[str, Any]) -> str:
    return canonical_draft_digest(dict(arguments))


__all__ = ["SendService", "canonical_sha256"]
