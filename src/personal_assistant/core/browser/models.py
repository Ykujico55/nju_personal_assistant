"""State machine, page snapshot and structured preview models for F07."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from personal_assistant.core.approvals.canonicalize import canonical_json, canonical_sha256
from personal_assistant.core.browser.errors import (
    BrowserLimitError,
    BrowserSessionStateError,
    PreviewExpiredError,
)
from personal_assistant.domain.enums import RiskLevel

#: Hard caps from the F07 contract.  They are enforced by the broker, the
#: companion and the executor before any page write or click.
MAX_SESSIONS_PER_TASK = 1
MAX_RENAVIGATIONS = 3
MAX_SNAPSHOT_BYTES = 1_048_576
MAX_FIELDS = 128
MAX_ATTACHMENTS = 20
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_COMMAND_DEADLINE_SECONDS = 30.0
DEFAULT_COMMAND_DEADLINE_SECONDS = 30.0
DEFAULT_PREVIEW_TTL_SECONDS = 300
PREVIEW_TTL_LIMIT_SECONDS = 300
MAX_FIELD_VALUE_CHARS = 10_000
MAX_ACTIONS = 32


class BrowserSessionState(StrEnum):
    """Supervised session lifecycle.

    Standard F07 acceptance stops at ``PREVIEW_READY``.  ``APPROVED`` and the
    states after it only exist for an optional, separately approved real submit.
    """

    REQUESTED = "REQUESTED"
    WAITING_USER = "WAITING_USER"
    AUTHENTICATED = "AUTHENTICATED"
    DISCOVERED = "DISCOVERED"
    PREPARING = "PREPARING"
    PREVIEW_READY = "PREVIEW_READY"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"
    SAFETY_PAUSED = "SAFETY_PAUSED"

    @property
    def terminal(self) -> bool:
        return self in {
            BrowserSessionState.SUCCEEDED,
            BrowserSessionState.FAILED,
            BrowserSessionState.CANCELLED,
        }


_ALLOWED_TRANSITIONS: dict[BrowserSessionState, frozenset[BrowserSessionState]] = {
    BrowserSessionState.REQUESTED: frozenset(
        {
            BrowserSessionState.WAITING_USER,
            BrowserSessionState.AUTHENTICATED,
            BrowserSessionState.DISCOVERED,
            BrowserSessionState.CANCELLED,
            BrowserSessionState.SAFETY_PAUSED,
            BrowserSessionState.UNKNOWN,
        }
    ),
    BrowserSessionState.WAITING_USER: frozenset(
        {
            BrowserSessionState.AUTHENTICATED,
            BrowserSessionState.CANCELLED,
            BrowserSessionState.SAFETY_PAUSED,
            BrowserSessionState.UNKNOWN,
        }
    ),
    BrowserSessionState.AUTHENTICATED: frozenset(
        {
            BrowserSessionState.WAITING_USER,
            BrowserSessionState.DISCOVERED,
            BrowserSessionState.PREPARING,
            BrowserSessionState.CANCELLED,
            BrowserSessionState.SAFETY_PAUSED,
            BrowserSessionState.UNKNOWN,
        }
    ),
    BrowserSessionState.DISCOVERED: frozenset(
        {
            BrowserSessionState.PREPARING,
            BrowserSessionState.WAITING_USER,
            BrowserSessionState.CANCELLED,
            BrowserSessionState.SAFETY_PAUSED,
            BrowserSessionState.UNKNOWN,
        }
    ),
    BrowserSessionState.PREPARING: frozenset(
        {
            BrowserSessionState.PREVIEW_READY,
            BrowserSessionState.DISCOVERED,
            BrowserSessionState.WAITING_USER,
            BrowserSessionState.CANCELLED,
            BrowserSessionState.SAFETY_PAUSED,
            BrowserSessionState.FAILED,
            BrowserSessionState.UNKNOWN,
        }
    ),
    BrowserSessionState.PREVIEW_READY: frozenset(
        {
            BrowserSessionState.APPROVED,
            BrowserSessionState.PREPARING,
            BrowserSessionState.WAITING_USER,
            BrowserSessionState.CANCELLED,
            BrowserSessionState.SAFETY_PAUSED,
            BrowserSessionState.FAILED,
            BrowserSessionState.UNKNOWN,
        }
    ),
    BrowserSessionState.APPROVED: frozenset(
        {
            BrowserSessionState.EXECUTING,
            BrowserSessionState.CANCELLED,
            BrowserSessionState.SAFETY_PAUSED,
            BrowserSessionState.FAILED,
            BrowserSessionState.UNKNOWN,
        }
    ),
    BrowserSessionState.EXECUTING: frozenset(
        {
            BrowserSessionState.SUCCEEDED,
            BrowserSessionState.FAILED,
            BrowserSessionState.UNKNOWN,
        }
    ),
    # UNKNOWN only leaves through read-only flow tracking or human adjudication.
    BrowserSessionState.UNKNOWN: frozenset(
        {
            BrowserSessionState.SUCCEEDED,
            BrowserSessionState.FAILED,
        }
    ),
    BrowserSessionState.SUCCEEDED: frozenset(),
    BrowserSessionState.FAILED: frozenset(),
    BrowserSessionState.CANCELLED: frozenset(),
    # A safety pause is resolved by the user with a fresh read/prepare pass.
    BrowserSessionState.SAFETY_PAUSED: frozenset(
        {
            BrowserSessionState.PREPARING,
            BrowserSessionState.DISCOVERED,
            BrowserSessionState.CANCELLED,
        }
    ),
}


def allowed_browser_transitions(
    state: BrowserSessionState,
) -> frozenset[BrowserSessionState]:
    return _ALLOWED_TRANSITIONS.get(state, frozenset())


def ensure_browser_transition(
    current: BrowserSessionState, target: BrowserSessionState
) -> None:
    if target not in allowed_browser_transitions(current):
        raise BrowserSessionStateError(
            f"invalid browser session transition {current.value} -> {target.value}"
        )


class FieldValueSource(StrEnum):
    PAGE = "PAGE"
    USER_INPUT = "USER_INPUT"
    EVIDENCE = "EVIDENCE"
    TEMPLATE = "TEMPLATE"
    DEFAULT = "DEFAULT"
    UNKNOWN = "UNKNOWN"


class FieldValidation(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class BrowserLimits:
    max_sessions_per_task: int = MAX_SESSIONS_PER_TASK
    max_renavigations: int = MAX_RENAVIGATIONS
    max_snapshot_bytes: int = MAX_SNAPSHOT_BYTES
    max_fields: int = MAX_FIELDS
    max_attachments: int = MAX_ATTACHMENTS
    max_attachment_bytes: int = MAX_ATTACHMENT_BYTES
    command_deadline_seconds: float = DEFAULT_COMMAND_DEADLINE_SECONDS


@dataclass(frozen=True, slots=True)
class RiskSignal:
    """A risk observation that may only raise, never lower, a classification."""

    code: str
    detail: str
    risk: RiskLevel


@dataclass(frozen=True, slots=True)
class PageField:
    """One live form control; ``locator`` is opaque adapter-owned data."""

    field_id: str
    locator: str
    kind: str
    value: str = ""
    name: str = ""
    required: bool = False
    readonly: bool = False
    options: tuple[str, ...] = ()
    max_length: int = 0
    known: bool = True


@dataclass(frozen=True, slots=True)
class PageAction:
    """A clickable action that an adapter explicitly allows on the page."""

    action_id: str
    locator: str
    label: str
    kind: str
    risk: RiskLevel = RiskLevel.EXTERNAL_WRITE
    known: bool = True


@dataclass(frozen=True, slots=True)
class PageLink:
    """A bounded, same-page link used for read-only app discovery."""

    text: str
    path: str


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    session_id: str
    url: str
    origin: str
    path: str
    title: str
    fingerprint: str
    captured_at: datetime
    fields: tuple[PageField, ...] = ()
    actions: tuple[PageAction, ...] = ()
    signals: tuple[RiskSignal, ...] = ()
    links: tuple[PageLink, ...] = ()
    text_digest: str = ""
    byte_size: int = 0
    truncated: bool = False
    scan_incomplete: bool = False
    authenticated: bool = True

    def field_by_id(self, field_id: str) -> PageField | None:
        for item in self.fields:
            if item.field_id == field_id:
                return item
        return None


@dataclass(frozen=True, slots=True)
class FieldChange:
    field_id: str
    locator: str
    label: str
    old_value: str
    new_value: str
    source: FieldValueSource = FieldValueSource.USER_INPUT
    confidence: float = 1.0
    validation: FieldValidation = FieldValidation.VALID
    evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        if len(self.new_value) > MAX_FIELD_VALUE_CHARS:
            raise BrowserLimitError("a field value exceeds the length limit")
        if not isinstance(self.source, FieldValueSource):
            raise BrowserLimitError("a field source must be a FieldValueSource")
        if not isinstance(self.validation, FieldValidation):
            raise BrowserLimitError("a field validation must be a FieldValidation")
        if not 0.0 <= self.confidence <= 1.0:
            raise BrowserLimitError("field confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class AttachmentPreview:
    name: str
    size_bytes: int
    sha256: str
    media_type: str = "application/octet-stream"

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 255:
            raise BrowserLimitError("invalid attachment name")
        if not 0 <= self.size_bytes <= MAX_ATTACHMENT_BYTES:
            raise BrowserLimitError("attachment size exceeds the limit")
        digest = self.sha256.lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise BrowserLimitError("attachment sha256 must be 64 lowercase hex characters")


_FIELD_DOCUMENT_KEYS = (
    "field_id",
    "locator",
    "label",
    "old_value",
    "new_value",
    "source",
    "confidence",
    "validation",
    "evidence_sha256",
)


def _field_document(change: FieldChange) -> dict[str, Any]:
    return {
        "field_id": change.field_id,
        "locator": change.locator,
        "label": change.label,
        "old_value": change.old_value,
        "new_value": change.new_value,
        "source": change.source.value,
        "confidence": round(float(change.confidence), 6),
        "validation": change.validation.value,
        "evidence_sha256": change.evidence_sha256,
    }


def _attachment_document(attachment: AttachmentPreview) -> dict[str, Any]:
    return {
        "name": attachment.name,
        "size_bytes": attachment.size_bytes,
        "sha256": attachment.sha256,
        "media_type": attachment.media_type,
    }


def canonical_preview_sha256(
    *,
    origin: str,
    app_id: str,
    transaction_id: str,
    adapter_id: str,
    adapter_version: str,
    extension_id: str,
    extension_version: str,
    page_fingerprint: str,
    risk: RiskLevel,
    consequences: str,
    fields: tuple[FieldChange, ...],
    attachments: tuple[AttachmentPreview, ...],
    target_action_id: str = "",
    target_method: str = "",
    target_origin: str = "",
    target_path: str = "",
) -> str:
    """SHA-256 over the canonical preview payload.

    Field and attachment order never changes the digest; any value, source,
    attachment hash, page fingerprint, transaction, adapter/extension version
    or submission target (action id, HTTP method, origin, path) change does.
    The nonce and the timestamps are deliberately excluded.
    """

    field_documents = sorted(
        (_field_document(item) for item in fields),
        key=lambda item: (item["field_id"], item["locator"]),
    )
    attachment_documents = sorted(
        (_attachment_document(item) for item in attachments),
        key=lambda item: (item["name"], item["sha256"], item["size_bytes"]),
    )
    return canonical_sha256(
        {
            "origin": origin,
            "app_id": app_id,
            "transaction_id": transaction_id,
            "adapter_id": adapter_id,
            "adapter_version": adapter_version,
            "extension_id": extension_id,
            "extension_version": extension_version,
            "page_fingerprint": page_fingerprint,
            "risk": risk.value,
            "consequences": consequences,
            "fields": field_documents,
            "attachments": attachment_documents,
            "target_action_id": target_action_id,
            "target_method": target_method,
            "target_origin": target_origin,
            "target_path": target_path,
        }
    )


@dataclass(frozen=True, slots=True)
class TransactionPreview:
    """The structured, bounded preview shown before any final submission."""

    session_id: str
    origin: str
    app_id: str
    transaction_id: str
    adapter_id: str
    adapter_version: str
    page_fingerprint: str
    risk: RiskLevel
    consequences: str
    fields: tuple[FieldChange, ...]
    canonical_payload_hash: str
    nonce: str
    generated_at: datetime
    expires_at: datetime
    missing_fields: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()
    attachments: tuple[AttachmentPreview, ...] = ()
    extension_id: str = ""
    extension_version: str = ""
    target_action_id: str = ""
    target_method: str = ""
    target_origin: str = ""
    target_path: str = ""

    def __post_init__(self) -> None:
        if len(self.fields) > MAX_FIELDS:
            raise BrowserLimitError("preview exceeds the field limit")
        if len(self.attachments) > MAX_ATTACHMENTS:
            raise BrowserLimitError("preview exceeds the attachment limit")
        if self.risk is RiskLevel.PROHIBITED:
            raise BrowserLimitError("a prohibited transaction can never reach a preview")
        if not self.nonce:
            raise BrowserLimitError("a preview requires a nonce")
        if self.expires_at <= self.generated_at:
            raise BrowserLimitError("preview expiry must be after generation")

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def require_fresh(self, now: datetime) -> None:
        if self.is_expired(now):
            raise PreviewExpiredError("the transaction preview has expired")

    def to_document(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "origin": self.origin,
            "app_id": self.app_id,
            "transaction_id": self.transaction_id,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "extension_id": self.extension_id,
            "extension_version": self.extension_version,
            "page_fingerprint": self.page_fingerprint,
            "risk": self.risk.value,
            "consequences": self.consequences,
            "fields": [_field_document(item) for item in self.fields],
            "missing_fields": list(self.missing_fields),
            "unknown_fields": list(self.unknown_fields),
            "attachments": [_attachment_document(item) for item in self.attachments],
            "canonical_payload_hash": self.canonical_payload_hash,
            "nonce": self.nonce,
            "generated_at": self.generated_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "target_action_id": self.target_action_id,
            "target_method": self.target_method,
            "target_origin": self.target_origin,
            "target_path": self.target_path,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> TransactionPreview:
        fields = tuple(
            FieldChange(
                field_id=str(item["field_id"]),
                locator=str(item["locator"]),
                label=str(item.get("label", "")),
                old_value=str(item.get("old_value", "")),
                new_value=str(item.get("new_value", "")),
                source=FieldValueSource(str(item.get("source", "UNKNOWN"))),
                confidence=float(item.get("confidence", 1.0)),
                validation=FieldValidation(str(item.get("validation", "VALID"))),
                evidence_sha256=(
                    str(item["evidence_sha256"])
                    if item.get("evidence_sha256") is not None
                    else None
                ),
            )
            for item in document.get("fields", [])
            if isinstance(item, dict) and _FIELD_DOCUMENT_KEYS[0] in item
        )
        attachments = tuple(
            AttachmentPreview(
                name=str(item["name"]),
                size_bytes=int(item["size_bytes"]),
                sha256=str(item["sha256"]),
                media_type=str(item.get("media_type", "application/octet-stream")),
            )
            for item in document.get("attachments", [])
            if isinstance(item, dict) and "sha256" in item
        )
        return cls(
            session_id=str(document["session_id"]),
            origin=str(document["origin"]),
            app_id=str(document.get("app_id", "")),
            transaction_id=str(document.get("transaction_id", "")),
            adapter_id=str(document["adapter_id"]),
            adapter_version=str(document["adapter_version"]),
            extension_id=str(document.get("extension_id", "")),
            extension_version=str(document.get("extension_version", "")),
            page_fingerprint=str(document["page_fingerprint"]),
            risk=RiskLevel(str(document["risk"])),
            consequences=str(document.get("consequences", "")),
            fields=fields,
            missing_fields=tuple(str(item) for item in document.get("missing_fields", [])),
            unknown_fields=tuple(str(item) for item in document.get("unknown_fields", [])),
            attachments=attachments,
            canonical_payload_hash=str(document["canonical_payload_hash"]),
            nonce=str(document["nonce"]),
            generated_at=_parse_time(document["generated_at"]),
            expires_at=_parse_time(document["expires_at"]),
            target_action_id=str(document.get("target_action_id", "")),
            target_method=str(document.get("target_method", "")),
            target_origin=str(document.get("target_origin", "")),
            target_path=str(document.get("target_path", "")),
        )


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@dataclass(frozen=True, slots=True)
class FillPlan:
    """The approved fill intent supplied by a transaction adapter.

    ``old_value`` entries are drift detectors: the executor refuses to type
    anything when the live page no longer matches the plan.
    """

    adapter_id: str
    adapter_version: str
    transaction_id: str
    expected_origin: str
    expected_page_fingerprint: str
    fields: tuple[FieldChange, ...]
    app_id: str = ""
    attachments: tuple[AttachmentPreview, ...] = ()
    consequences: str = ""

    def __post_init__(self) -> None:
        if len(self.fields) > MAX_FIELDS:
            raise BrowserLimitError("fill plan exceeds the field limit")
        if len(self.attachments) > MAX_ATTACHMENTS:
            raise BrowserLimitError("fill plan exceeds the attachment limit")
        if not self.adapter_id or not self.adapter_version:
            raise BrowserLimitError("fill plan requires an adapter identity")
        if not self.transaction_id:
            raise BrowserLimitError("fill plan requires a transaction id")
        for change in self.fields:
            if change.source is FieldValueSource.UNKNOWN:
                raise BrowserLimitError("every planned value needs a known source")

    def field_ids(self) -> tuple[str, ...]:
        return tuple(item.field_id for item in self.fields)


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    state: BrowserSessionState
    receipt: Mapping[str, Any] | None = None
    reference: str = ""
    diagnostic_code: str = ""

    @property
    def unknown(self) -> bool:
        return self.state is BrowserSessionState.UNKNOWN


def with_transition(
    record: Any, target: BrowserSessionState, **updates: Any
) -> Any:
    """Return ``record`` (a slotted frozen dataclass) transitioned to ``target``."""

    current = record.state
    ensure_browser_transition(current, target)
    return replace(record, state=target, **updates)


__all__ = [
    "DEFAULT_COMMAND_DEADLINE_SECONDS",
    "DEFAULT_PREVIEW_TTL_SECONDS",
    "MAX_ACTIONS",
    "MAX_ATTACHMENTS",
    "MAX_ATTACHMENT_BYTES",
    "MAX_COMMAND_DEADLINE_SECONDS",
    "MAX_FIELDS",
    "MAX_FIELD_VALUE_CHARS",
    "MAX_RENAVIGATIONS",
    "MAX_SESSIONS_PER_TASK",
    "MAX_SNAPSHOT_BYTES",
    "PREVIEW_TTL_LIMIT_SECONDS",
    "AttachmentPreview",
    "BrowserLimits",
    "BrowserSessionState",
    "FieldChange",
    "FieldValidation",
    "FieldValueSource",
    "FillPlan",
    "PageAction",
    "PageField",
    "PageLink",
    "PageSnapshot",
    "RiskSignal",
    "SubmissionOutcome",
    "TransactionPreview",
    "allowed_browser_transitions",
    "canonical_json",
    "canonical_preview_sha256",
    "ensure_browser_transition",
    "with_transition",
]
