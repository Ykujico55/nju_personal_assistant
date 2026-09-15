"""Small immutable value objects and recoverable task state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .enums import RetryPolicy, RiskLevel, TaskState, ToolOutcomeKind
from .errors import ValidationError


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AttachmentDigest:
    """Attachment identity used by an exact approval snapshot."""

    name: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValidationError("attachment name is required")
        digest = self.sha256.lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValidationError("attachment sha256 must be 64 hexadecimal characters")
        if self.size_bytes < 0:
            raise ValidationError("attachment size_bytes cannot be negative")
        object.__setattr__(self, "sha256", digest)


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """An immutable capability declaration published in a registry snapshot."""

    id: str
    version: str
    extension_id: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    risk: RiskLevel
    extension_version: str | None = None
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    timeout_seconds: float = 60.0
    retry_policy: RetryPolicy = RetryPolicy.NONE
    data_classes_in: frozenset[str] = field(default_factory=frozenset)
    data_classes_out: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for name, value in (
            ("id", self.id),
            ("version", self.version),
            ("extension_id", self.extension_id),
        ):
            if not value or not value.strip():
                raise ValidationError(f"tool {name} is required")
        if self.timeout_seconds <= 0:
            raise ValidationError("tool timeout_seconds must be positive")
        # This is the non-disableable per-call emergency ceiling.  Browser wait
        # tools may opt up to it; ordinary tools should keep the 60 second default.
        if self.timeout_seconds > 15 * 60:
            raise ValidationError("tool timeout_seconds cannot exceed 15 minutes")
        if not isinstance(self.risk, RiskLevel):
            raise ValidationError("tool risk must be a RiskLevel")
        if self.extension_version is None:
            # A legacy descriptor without a separate extension package version is
            # conservatively bound to its tool version.
            object.__setattr__(self, "extension_version", self.version)
        elif not self.extension_version.strip():
            raise ValidationError("extension_version cannot be blank")


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A validated-planner candidate; the gateway still performs all checks."""

    tool_id: str
    tool_version: str
    arguments: Mapping[str, Any]
    task_id: str
    target: Any = None
    attachments: tuple[AttachmentDigest, ...] = ()
    granted_capabilities: frozenset[str] = field(default_factory=frozenset)
    workflow_allowed_tools: frozenset[str] = field(default_factory=frozenset)
    approval_id: str | None = None
    idempotency_key: str | None = None
    form_version: str | None = None

    def __post_init__(self) -> None:
        if not self.tool_id or not self.tool_version or not self.task_id:
            raise ValidationError("tool_id, tool_version and task_id are required")


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    kind: ToolOutcomeKind
    result: Any = None
    message: str | None = None
    reference_id: str | None = None
    approval_id: str | None = None
    extension_id: str | None = None
    retryable: bool = False

    @classmethod
    def success(cls, result: Any) -> ToolOutcome:
        return cls(ToolOutcomeKind.SUCCESS, result=result)

    @classmethod
    def failed(cls, message: str, *, retryable: bool = False) -> ToolOutcome:
        return cls(ToolOutcomeKind.FAILED, message=message, retryable=retryable)

    @classmethod
    def unknown(cls, reference_id: str, message: str | None = None) -> ToolOutcome:
        return cls(
            ToolOutcomeKind.OUTCOME_UNKNOWN,
            message=message,
            reference_id=reference_id,
            retryable=False,
        )


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    objective: str
    state: TaskState = TaskState.CREATED
    version: int = 0
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.id or not self.objective.strip():
            raise ValidationError("task id and objective are required")
        if self.version < 0:
            raise ValidationError("task version cannot be negative")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class TaskRun:
    """Durable facts needed to resume one active execution segment."""

    id: str
    task_id: str
    objective: str
    state: TaskState = TaskState.RUNNING
    version: int = 0
    segment_number: int = 1
    active_started_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    consecutive_errors: int = 0
    no_progress_rounds: int = 0
    same_call_without_progress: int = 0
    last_call_fingerprint: str | None = None
    last_observation_fingerprint: str | None = None
    waiting_reference: str | None = None
    pause_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.task_id or not self.objective.strip():
            raise ValidationError("run id, task_id and objective are required")
        if self.version < 0 or self.segment_number < 1:
            raise ValidationError("invalid run version or segment number")
        for name in (
            "consecutive_errors",
            "no_progress_rounds",
            "same_call_without_progress",
        ):
            if getattr(self, name) < 0:
                raise ValidationError(f"{name} cannot be negative")
        _require_aware(self.active_started_at, "active_started_at")
        _require_aware(self.updated_at, "updated_at")
