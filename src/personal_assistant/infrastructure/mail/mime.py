"""Bounded MIME building and parsing helpers.

Message bodies are untrusted user input.  Parsing never executes content, never
decompresses archives and is bounded in depth, part count and decoded bytes so a
malformed or adversarial message cannot exhaust the host.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.generator import BytesGenerator
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses, parsedate_to_datetime

from personal_assistant.core.mail import MailError, MailErrorCode

MAX_PARTS = 256
MAX_DEPTH = 16
MAX_DECODED_BODY_BYTES = 4 * 1024 * 1024
MAX_ATTACHMENTS = 64
MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024

_FILENAME_UNSAFE = re.compile(r"[\x00-\x1f\x7f<>:\"/\\|?*]")
_SUBJECT_PREFIX = re.compile(r"^\s*(?:(?:re|fwd?|aw|sv|答复|回复)\s*:\s*)+", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
_MESSAGE_ID = re.compile(r"^<[^<>\s@]{1,200}@[^<>\s@]{1,200}>$")


@dataclass(frozen=True, slots=True)
class ParsedAttachment:
    filename: str
    media_type: str
    sha256: str
    size_bytes: int
    content: bytes


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    message_id: str | None
    subject: str
    from_address: str | None
    to_addresses: tuple[str, ...]
    cc_addresses: tuple[str, ...]
    bcc_addresses: tuple[str, ...]
    sent_at: str | None
    body_text: str
    attachments: tuple[ParsedAttachment, ...] = field(default_factory=tuple)
    truncated: bool = False


def normalized_subject(value: str | None) -> str:
    text = _WHITESPACE.sub(" ", (value or "").strip())
    return _SUBJECT_PREFIX.sub("", text).strip().lower()


def normalize_body_text(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


def sanitize_filename(value: str) -> str:
    """Reduce an attachment filename to a safe basename (no traversal)."""

    candidate = (value or "").replace("\\", "/").split("/")[-1]
    candidate = _FILENAME_UNSAFE.sub("_", candidate).strip().strip(".")
    if candidate in {"", ".", ".."}:
        return "attachment"
    return candidate[:180]


def validate_message_id(value: str) -> str:
    candidate = value.strip()
    if "\r" in candidate or "\n" in candidate or _MESSAGE_ID.fullmatch(candidate) is None:
        raise ValueError("message id must be a bracketed addr-spec without CR/LF")
    return candidate


def canonical_content_hash(
    *,
    message_id: str | None,
    subject: str | None,
    from_address: str | None,
    to_addresses: Sequence[str],
    sent_at: str | None,
    body_text: str,
    attachment_hashes: Sequence[str] = (),
    size_bytes: int = 0,
    truncated: bool = False,
) -> str:
    """Stable auxiliary dedupe identity for one logical message."""

    payload = {
        "message_id": (message_id or "").strip().lower(),
        "subject": normalized_subject(subject),
        "from": (from_address or "").strip().lower(),
        "to": sorted({item.strip().lower() for item in to_addresses}),
        "sent_at": (sent_at or "").strip(),
        "body": normalize_body_text(body_text),
        "attachments": sorted(attachment_hashes),
        "size_bytes": size_bytes if truncated else 0,
        "truncated": truncated,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def dedupe_key(*, message_id: str | None, content_hash: str) -> str:
    """Auxiliary identity: Message-ID *and* normalized content must both match.

    Binding the content hash means a reused or forged Message-ID with different
    bytes creates a distinct logical message instead of silently discarding the
    new body.
    """

    if message_id and message_id.strip():
        bound = (message_id.strip().lower() + "|" + content_hash).encode("utf-8")
        return "mid:" + hashlib.sha256(bound).hexdigest()
    return "content:" + content_hash


def parse_message(raw: bytes, *, max_body_bytes: int = MAX_DECODED_BODY_BYTES) -> ParsedMessage:
    """Parse one RFC822 message under strict bounds; never raises on MIME defects."""

    truncated = False
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception:  # noqa: BLE001 - malformed MIME is data, not a crash
        return ParsedMessage(
            message_id=None,
            subject="",
            from_address=None,
            to_addresses=(),
            cc_addresses=(),
            bcc_addresses=(),
            sent_at=None,
            body_text="",
            truncated=True,
        )

    texts: list[str] = []
    attachments: list[ParsedAttachment] = []
    seen = 0
    remaining = max_body_bytes

    def visit(part: Message, depth: int) -> None:
        nonlocal seen, remaining, truncated
        seen += 1
        if seen > MAX_PARTS or depth > MAX_DEPTH:
            truncated = True
            return
        if part.is_multipart():
            content_type = (part.get_content_type() or "").lower()
            if content_type == "message/rfc822":
                # Do not recurse into attached messages; treat them as opaque.
                payload = part.get_payload()
                if isinstance(payload, list):
                    for child in payload:
                        if isinstance(child, Message):
                            data = child.as_bytes()
                            attachments.append(_attachment(part, data))
                return
            for child in part.get_payload():
                if isinstance(child, Message):
                    visit(child, depth + 1)
            return
        payload_raw = part.get_payload(decode=True)
        if isinstance(payload_raw, bytes):
            payload = payload_raw
        elif isinstance(payload_raw, str):
            payload = payload_raw.encode("utf-8", "replace")
        else:
            raw_payload = part.get_payload()
            payload = (
                raw_payload.encode("utf-8", "replace")
                if isinstance(raw_payload, str)
                else b""
            )
        if len(payload) > MAX_ATTACHMENT_BYTES:
            truncated = True
            payload = payload[:MAX_ATTACHMENT_BYTES]
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if disposition == "attachment" or filename is not None:
            if len(attachments) < MAX_ATTACHMENTS:
                attachments.append(_attachment(part, payload))
            else:
                truncated = True
            return
        if part.get_content_maintype() == "text" and remaining > 0:
            text = payload[:remaining].decode(part.get_content_charset() or "utf-8", "replace")
            remaining -= len(text.encode("utf-8", "replace"))
            texts.append(text)

    visit(message, 0)

    headers = message
    references = headers.get("References")
    del references
    return ParsedMessage(
        message_id=_header_address(headers.get("Message-ID")),
        subject=_header_text(headers.get("Subject")),
        from_address=_first_address(_header_text(headers.get("From"))),
        to_addresses=_addresses(headers.get("To")),
        cc_addresses=_addresses(headers.get("Cc")),
        bcc_addresses=_addresses(headers.get("Bcc")),
        sent_at=_header_date(headers.get("Date")),
        body_text="\n".join(texts),
        attachments=tuple(attachments),
        truncated=truncated,
    )


def parse_envelope(raw: bytes) -> dict[str, object]:
    """Header-only summary used to compare actual bytes with an approval."""

    parsed = parse_message(raw)
    return {
        "message_id": parsed.message_id,
        "subject": parsed.subject,
        "from_address": parsed.from_address,
        "to_addresses": list(parsed.to_addresses),
        "cc_addresses": list(parsed.cc_addresses),
        "bcc_addresses": list(parsed.bcc_addresses),
        "attachment_hashes": [item.sha256 for item in parsed.attachments],
    }


def build_message_bytes(
    *,
    message_id: str,
    from_address: str,
    to_addresses: Sequence[str],
    cc_addresses: Sequence[str],
    bcc_addresses: Sequence[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    sent_at: datetime | None = None,
    in_reply_to: str | None = None,
    references: Sequence[str] = (),
    attachments: Sequence[tuple[str, str, bytes]] = (),
) -> bytes:
    """Build deterministic-enough RFC822 bytes for one draft version.

    The bytes are built once at draft time and hashed; the approval binds that
    exact hash and the executor sends the stored artifact without rebuilding.
    """

    validate_message_id(message_id)
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = from_address
    if to_addresses:
        message["To"] = ", ".join(to_addresses)
    if cc_addresses:
        message["Cc"] = ", ".join(cc_addresses)
    message["Subject"] = subject
    message["Date"] = format_datetime(sent_at or datetime.now(UTC))
    message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = " ".join(references)

    if body_html:
        message.set_content(body_text)
        message.add_alternative(body_html, subtype="html")
    else:
        message.set_content(body_text)
    for filename, media_type, data in attachments:
        maintype, _, subtype = media_type.partition("/")
        if not maintype or not subtype:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=sanitize_filename(filename),
        )
    buffer = io.BytesIO()
    BytesGenerator(buffer, policy=policy.SMTP, mangle_from_=False).flatten(
        message, linesep="\r\n"
    )
    return buffer.getvalue()


def _attachment(part: Message, payload: bytes) -> ParsedAttachment:
    filename = sanitize_filename(part.get_filename() or "attachment")
    return ParsedAttachment(
        filename=filename,
        media_type=(part.get_content_type() or "application/octet-stream").lower(),
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        content=payload,
    )


def _header_text(value: object) -> str:
    if value is None:
        return ""
    header = getattr(value, "header", None)
    if isinstance(header, str) and header:
        return header
    return str(value)


def _header_address(value: object) -> str | None:
    text = _header_text(value).strip()
    return text or None


def _first_address(value: str) -> str | None:
    addresses = getaddresses([value])
    return addresses[0][1] if addresses and addresses[0][1] else None


def _addresses(value: object) -> tuple[str, ...]:
    text = _header_text(value)
    if not text:
        return ()
    return tuple(address for _, address in getaddresses([text]) if address)


def _header_date(value: object) -> str | None:
    text = _header_text(value).strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def enforce_message_size(raw: bytes, *, max_bytes: int) -> None:
    if len(raw) > max_bytes:
        raise MailError(MailErrorCode.MESSAGE_TOO_LARGE, "message exceeds the size limit")


__all__ = [
    "MAX_ATTACHMENTS",
    "MAX_DEPTH",
    "MAX_PARTS",
    "ParsedAttachment",
    "ParsedMessage",
    "build_message_bytes",
    "canonical_content_hash",
    "dedupe_key",
    "enforce_message_size",
    "normalize_body_text",
    "normalized_subject",
    "parse_envelope",
    "parse_message",
    "sanitize_filename",
    "validate_message_id",
]
