"""Serializable extension contract models.

Only JSON-shaped values cross the process boundary.  Handles identify host-owned
resources; they intentionally expose neither credentials nor absolute host paths.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum, StrEnum
from types import UnionType
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class RiskLevel(StrEnum):
    READ = "READ"
    INTERNAL_WRITE = "INTERNAL_WRITE"
    EXTERNAL_WRITE = "EXTERNAL_WRITE"
    PROHIBITED = "PROHIBITED"


class Outcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE = "RETRYABLE"
    PERMANENT = "PERMANENT"
    NEEDS_USER_ACTION = "NEEDS_USER_ACTION"
    UNKNOWN = "UNKNOWN"


class ScheduleMisfirePolicy(StrEnum):
    SKIP = "skip"
    COALESCE = "coalesce"
    CATCH_UP = "catch_up"


@dataclass(frozen=True, slots=True)
class ArtifactHandle:
    id: str
    content_hash: str
    media_type: str
    size_bytes: int
    expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class ContextHandle:
    id: str
    content_hash: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class CapabilityHandle:
    """A short-lived, scoped broker grant.  It is not a raw secret."""

    id: str
    capability: str
    extension_id: str
    allowed_operations: tuple[str, ...]
    expires_at: str


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    protocol_version: str
    extension_id: str
    extension_version: str
    data_namespace: str
    manifest_schema_hash: str = ""
    non_secret_config: Mapping[str, JsonValue] = field(default_factory=dict)
    capability_handles: tuple[CapabilityHandle, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtensionInfo:
    id: str
    version: str
    protocol_version: str
    slots: tuple[str, ...]
    schema_hash: str


@dataclass(frozen=True, slots=True)
class HealthReport:
    healthy: bool
    status: str
    details: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DrainReport:
    drained: bool
    active_calls: int
    checkpoint_handles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    id: str
    risk: RiskLevel
    input_schema: Mapping[str, JsonValue]
    output_schema: Mapping[str, JsonValue]
    description: str = ""


@dataclass(frozen=True, slots=True)
class InvocationContext:
    task_id: str
    run_id: str
    deadline: str
    idempotency_key: str
    context_handles: tuple[ContextHandle, ...] = ()
    artifact_handles: tuple[ArtifactHandle, ...] = ()
    capability_handles: tuple[CapabilityHandle, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolResult:
    outcome: Outcome
    output: Mapping[str, JsonValue] = field(default_factory=dict)
    artifact_handles: tuple[ArtifactHandle, ...] = ()
    external_receipt: Mapping[str, JsonValue] | None = None
    retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ContextQuery:
    text: str
    limit: int = 10
    filters: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Evidence:
    text: str
    source_id: str
    content_hash: str
    source_uri: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EventSourceDescriptor:
    id: str
    event_types: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True, slots=True)
class PollRequest:
    source_id: str
    cursor: str | None
    deadline: str
    limit: int = 100


@dataclass(frozen=True, slots=True)
class DomainEvent:
    type: str
    source_dedupe_key: str
    occurred_at: str
    payload: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PollResult:
    events: tuple[DomainEvent, ...]
    source_dedupe_keys: tuple[str, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        event_keys = tuple(event.source_dedupe_key for event in self.events)
        if len(set(self.source_dedupe_keys)) != len(self.source_dedupe_keys):
            raise ValueError("source_dedupe_keys must be unique")
        if set(event_keys) != set(self.source_dedupe_keys):
            raise ValueError("source_dedupe_keys must match the returned events")


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    id: str
    version: str
    steps: tuple[Mapping[str, JsonValue], ...]


@dataclass(frozen=True, slots=True)
class ScheduleDefinition:
    id: str
    timezone: str
    misfire_policy: ScheduleMisfirePolicy
    cron: str | None = None
    interval_seconds: int | None = None

    def __post_init__(self) -> None:
        if (self.cron is None) == (self.interval_seconds is None):
            raise ValueError("provide exactly one of cron or interval_seconds")
        if self.interval_seconds is not None and self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class NotificationRequest:
    notification_id: str
    title: str
    body: str
    data_classification: str
    target_handle: str


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    outcome: Outcome
    provider_message_id: str | None = None
    delivered_at: str | None = None


@dataclass(frozen=True, slots=True)
class MigrationDescriptor:
    version: int
    checksum: str
    description: str


@dataclass(frozen=True, slots=True)
class FormSchemaDescriptor:
    id: str
    json_schema: Mapping[str, JsonValue]
    ui_schema: Mapping[str, JsonValue] = field(default_factory=dict)
    field_sensitivity: Mapping[str, str] = field(default_factory=dict)


def to_jsonable(value: Any) -> JsonValue:
    """Convert an SDK contract value into a strict JSON-shaped value."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return cast(JsonValue, value)
    if isinstance(value, Enum):
        return cast(JsonValue, value.value)
    if is_dataclass(value):
        return {
            key: to_jsonable(item)
            for key, item in asdict(cast(Any, value)).items()
        }
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def from_mapping[T](model: type[T], value: Mapping[str, Any]) -> T:
    """Construct nested SDK dataclasses from a decoded JSON object."""

    if not is_dataclass(model):
        raise TypeError(f"{model!r} is not a dataclass type")
    hints = get_type_hints(model)
    kwargs: dict[str, Any] = {}
    allowed = {item.name for item in fields(model)}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unexpected fields for {model.__name__}: {sorted(unknown)}")
    for item in fields(model):
        if item.name in value:
            kwargs[item.name] = _coerce(hints.get(item.name, item.type), value[item.name])
    return model(**kwargs)


def _coerce(annotation: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, UnionType):
        for candidate in args:
            if candidate is type(None):
                continue
            try:
                return _coerce(candidate, value)
            except (TypeError, ValueError):
                pass
        return value
    if origin in (tuple, list):
        element = args[0] if args else Any
        converted = [_coerce(element, item) for item in value]
        return tuple(converted) if origin is tuple else converted
    if origin in (dict, Mapping):
        return dict(value)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if isinstance(annotation, type) and is_dataclass(annotation):
        return from_mapping(annotation, value)
    return value
