"""Immutable MIME preparation through public host capabilities; never sends SMTP."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import zipfile
from collections.abc import Mapping
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime
from typing import Any

from personal_assistant_sdk import HostArtifactClient, HostMailClient

from .domain import ResumeRef, canonical_digest
from .sources import normalize_recipient

MAX_RESUME_BYTES = 512 * 1024
SUPPORTED_MEDIA = frozenset(
    {
        "application/pdf",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)


class MailPlanError(ValueError):
    def __init__(self, code: str = "MAIL_MATERIAL_INVALID") -> None:
        self.code = code
        super().__init__(code)


def _header(value: object, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise MailPlanError()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise MailPlanError()
    return value


def _resume(arguments: Mapping[str, Any]) -> ResumeRef:
    try:
        return ResumeRef(**dict(arguments))
    except (TypeError, ValueError):
        raise MailPlanError("RESUME_REFERENCE_INVALID") from None


class CareerMail:
    def __init__(
        self, mail: HostMailClient | None, artifacts: HostArtifactClient | None, account_id: str
    ) -> None:
        self.mail = mail
        self.artifacts = artifacts
        self.account_id = account_id

    def _artifacts(self) -> HostArtifactClient:
        if self.artifacts is None:
            raise MailPlanError("ARTIFACT_CAPABILITY_REQUIRED")
        return self.artifacts

    async def import_resume(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        filename = _header(arguments.get("filename"), 180)
        media_type = arguments.get("media_type")
        if media_type not in SUPPORTED_MEDIA:
            raise MailPlanError("RESUME_MEDIA_UNSUPPORTED")
        encoded = arguments.get("data_base64")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_RESUME_BYTES + 2) // 3):
            raise MailPlanError("RESUME_TOO_LARGE")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise MailPlanError("RESUME_ENCODING_INVALID") from None
        if not 0 < len(data) <= MAX_RESUME_BYTES:
            raise MailPlanError("RESUME_TOO_LARGE")
        try:
            if media_type == "application/pdf" and not data.startswith(b"%PDF-"):
                raise ValueError()
            if media_type == "text/plain":
                data.decode("utf-8")
                if b"\x00" in data:
                    raise ValueError()
            if media_type.startswith("application/vnd."):
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    names = archive.namelist()
                    if len(names) > 1000 or "word/document.xml" not in names:
                        raise ValueError()
        except (ValueError, zipfile.BadZipFile, UnicodeError):
            raise MailPlanError("RESUME_FORMAT_INVALID") from None
        digest = hashlib.sha256(data).hexdigest()
        reference = ResumeRef(filename, digest, len(data), str(media_type))
        artifact = await self._artifacts().put(
            data, media_type=str(media_type), sensitivity="PERSONAL"
        )
        if artifact.content_hash != digest or artifact.size_bytes != len(data):
            raise MailPlanError("ARTIFACT_INTEGRITY_ERROR")
        return {**reference.as_dict(), "artifact_id": artifact.id}

    async def prepare(
        self, arguments: Mapping[str, Any], *, request_id: str, task_id: str
    ) -> dict[str, Any]:
        if self.mail is None or not self.account_id:
            raise MailPlanError("MAIL_ACCOUNT_REQUIRED")
        recipient = normalize_recipient({"email": arguments.get("to")})["email"]
        subject = _header(arguments.get("subject"), 200)
        body = arguments.get("body")
        if not isinstance(body, str) or not body.strip() or len(body) > 20_000 or "\x00" in body:
            raise MailPlanError("MAIL_BODY_INVALID")
        try:
            prepared_at = datetime.fromisoformat(str(arguments["prepared_at"]))
            if prepared_at.tzinfo is None:
                raise ValueError()
            date_header = format_datetime(prepared_at)
        except (KeyError, ValueError, OverflowError):
            raise MailPlanError("PREPARED_TIME_REQUIRED") from None
        ref = _resume(arguments.get("resume", {}))
        if not ref.artifact_id:
            raise MailPlanError("RESUME_ARTIFACT_REQUIRED")
        data = await self._artifacts().read(ref.artifact_id)
        if len(data) != ref.size_bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise MailPlanError("RESUME_CHANGED")
        account = await self.mail.account(self.account_id)
        if not account.send_enabled:
            raise MailPlanError("MAIL_SEND_DISABLED")
        sender = normalize_recipient({"email": account.address})["email"]
        identity = canonical_digest(
            {
                "task_id": task_id,
                "request_id": request_id,
                "account_fingerprint": account.fingerprint,
                "to": recipient,
                "subject": subject,
                "body": body,
                "resume": ref.as_dict(),
                "date": date_header,
            }
        )
        action = "career_" + identity
        message_id = "<" + action + "@personal-assistant.invalid>"
        draft_id = "career_draft_" + identity
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = subject
        message["Date"] = date_header
        message["Message-ID"] = message_id
        message["X-Career-Action-ID"] = action
        message["X-Career-Draft-ID"] = draft_id
        message.set_content(body)
        main, sub = ref.media_type.split("/", 1)
        message.add_attachment(data, maintype=main, subtype=sub, filename=ref.filename)
        message.set_boundary("career-" + identity)
        raw = message.as_bytes()
        artifact = await self._artifacts().put(
            raw, media_type="message/rfc822", sensitivity="PERSONAL"
        )
        digest = hashlib.sha256(raw).hexdigest()
        if artifact.content_hash != digest:
            raise MailPlanError("ARTIFACT_INTEGRITY_ERROR")
        return {
            "local_action_id": action,
            "account_id": account.account_id,
            "account_fingerprint": account.fingerprint,
            "from_address": sender,
            "draft_id": draft_id,
            "draft_version": 1,
            "message_id": message_id,
            "mime_artifact_id": artifact.id,
            "mime_sha256": digest,
            "to": [recipient],
            "cc": [],
            "bcc": [],
            "subject": subject,
            "attachment_hashes": [ref.sha256],
        }

    async def preview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self.mail is None or arguments.get("account_id") != self.account_id:
            raise MailPlanError("MAIL_ACCOUNT_REQUIRED")
        account = await self.mail.account(self.account_id)
        if (
            not account.send_enabled
            or arguments.get("account_fingerprint") != account.fingerprint
            or arguments.get("from_address") != account.address
        ):
            raise MailPlanError("MAIL_ACCOUNT_CHANGED")
        raw = await self._artifacts().read(_header(arguments.get("mime_artifact_id")))
        if hashlib.sha256(raw).hexdigest() != arguments.get("mime_sha256"):
            raise MailPlanError("MIME_CHANGED")
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        if (
            type(arguments.get("draft_version")) is not int
            or arguments.get("draft_version") != 1
            or parsed["X-Career-Action-ID"] != arguments.get("local_action_id")
            or parsed["X-Career-Draft-ID"] != arguments.get("draft_id")
            or parsed["Message-ID"] != arguments.get("message_id")
            or str(parsed["From"]) != arguments.get("from_address")
            or [str(parsed["To"])] != arguments.get("to")
            or str(parsed["Subject"]) != arguments.get("subject")
            or arguments.get("cc") != []
            or arguments.get("bcc") != []
        ):
            raise MailPlanError("MAIL_BINDING_CHANGED")
        hashes = []
        for item in parsed.iter_attachments():
            payload = item.get_payload(decode=True)
            if not isinstance(payload, bytes):
                raise MailPlanError("RESUME_CHANGED")
            hashes.append(hashlib.sha256(payload).hexdigest())
        if not hashes or hashes != arguments.get("attachment_hashes"):
            raise MailPlanError("RESUME_CHANGED")
        return dict(arguments)
