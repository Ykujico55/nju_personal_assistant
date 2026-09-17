from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from personal_assistant.domain import ValidationError


class DataClassification(StrEnum):
    PUBLIC = "PUBLIC"
    PERSONAL = "PERSONAL"
    SENSITIVE = "SENSITIVE"
    SECRET = "SECRET"


def classification_of(value: object) -> DataClassification | None:
    """Canonicalize any classification-like value; ``None`` means invalid.

    A foreign ``StrEnum`` member or a plain ``"SECRET"`` string must not slip
    through identity checks, so values are normalized by value rather than by
    type.
    """

    if isinstance(value, DataClassification):
        return value
    if not isinstance(value, str):
        return None
    try:
        return DataClassification(value)
    except ValueError:
        return None


def is_secret_classification(value: object) -> bool:
    """Fail closed: an unknown or invalid classification counts as SECRET."""

    canonical = classification_of(value)
    return canonical is None or canonical is DataClassification.SECRET


@dataclass(frozen=True, slots=True)
class ContextField:
    name: str
    value: str
    classification: DataClassification
    source: str

    def __post_init__(self) -> None:
        for name, value in (
            ("name", self.name),
            ("value", self.value),
            ("source", self.source),
        ):
            if not isinstance(value, str):
                raise ValidationError(f"context field {name} must be a string")
        if not self.name.strip() or not self.source.strip():
            raise ValidationError("context field name and source are required")
        # Canonicalize at the boundary: callers may hand over a plain string or
        # a foreign ``StrEnum`` member, and every later identity check must see
        # a real ``DataClassification``.
        canonical = classification_of(self.classification)
        if canonical is None:
            raise ValidationError("context field classification is invalid")
        if canonical is not self.classification:
            object.__setattr__(self, "classification", canonical)


@dataclass(frozen=True, slots=True)
class RecipientIdentity:
    """The concrete receiving endpoint a disclosure consent is bound to.

    ``provider_id`` alone is a reusable label: an operator can repoint it at a
    different endpoint, adapter or model.  A consent binds this full identity so
    an old consent can never authorize a new receiver.
    """

    provider_id: str
    adapter: str
    endpoint: str
    model_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("provider_id", self.provider_id),
            ("adapter", self.adapter),
            ("endpoint", self.endpoint),
            ("model_id", self.model_id),
        ):
            if not value or not value.strip():
                raise ValidationError(f"recipient {name} is required")


def coerce_context_field(field: object) -> ContextField:
    """Normalize any field-like object into a validated ``ContextField``.

    Duck-typed fields are accepted only when every component has the right
    runtime type; anything else fails closed with ``ValidationError`` before it
    can reach a model, an audit record or a traceback.  Attribute access that
    raises (a hostile property) is treated the same way, without chaining the
    original exception.
    """

    if isinstance(field, ContextField):
        return field
    candidate: Any = field
    name: object = None
    value: object = None
    source: object = None
    classification: DataClassification | None = None
    try:
        name = candidate.name
        value = candidate.value
        source = candidate.source
        classification = classification_of(candidate.classification)
    except Exception:  # noqa: BLE001 - hostile attribute access, fail closed
        name = value = source = None
        classification = None
    if (
        not isinstance(name, str)
        or not isinstance(value, str)
        or not isinstance(source, str)
        or classification is None
    ):
        # Drop the untrusted object before raising so the error frame keeps no
        # reference to it and no exception is chained.
        field = None
        candidate = None
        name = value = source = None
        raise ValidationError("model request fields must be valid context fields")
    return ContextField(
        name=name, value=value, classification=classification, source=source
    )


@dataclass(frozen=True, slots=True)
class ModelRequest:
    purpose: str
    instruction: str
    fields: tuple[ContextField, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Full runtime validation: a non-string instruction used to reach
        # ``render_request_text`` and raise a bare ``AttributeError`` whose
        # frames still held the sensitive field values.
        for name, value in (
            ("purpose", self.purpose),
            ("instruction", self.instruction),
        ):
            if not isinstance(value, str):
                object.__setattr__(self, "purpose", "")
                object.__setattr__(self, "instruction", "")
                object.__setattr__(self, "fields", ())
                raise ValidationError(f"model request {name} must be a string")
        try:
            items = tuple(self.fields)
        except TypeError:
            items = ()
            invalid = True
        else:
            invalid = False
        if not invalid:
            try:
                normalized = tuple(coerce_context_field(item) for item in items)
            except Exception:  # noqa: BLE001 - any hostile field object
                normalized = ()
                invalid = True
        else:
            normalized = ()
        if invalid:
            # Scrub every reference to the untrusted container/field before
            # raising, so the error frame holds no sensitive value.
            object.__setattr__(self, "fields", ())
            items = ()
            normalized = ()
            raise ValidationError("model request fields must be valid context fields")
        # Unconditional write-back: never rely on ``==`` (a duck field's
        # ``__eq__`` could claim equality and keep a mutable object alive past
        # the consent binding).
        object.__setattr__(self, "fields", normalized)


@dataclass(frozen=True, slots=True)
class ModelOutput:
    text: str
    provider_id: str
    model_id: str
    usage: dict[str, int] = field(default_factory=dict)


# Usage counters are the only response metadata persisted beyond the completion
# text.  Only these well-known, adapter-owned keys may ever reach an audit
# record; anything else a provider returns is dropped rather than trusted.
AUDITABLE_USAGE_KEYS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_eval_count",
        "eval_count",
    }
)


def bounded_usage(payload: Any, allowed: frozenset[str]) -> dict[str, int]:
    """Filter provider usage down to non-negative integers under ``allowed``."""

    if not isinstance(payload, dict):
        return {}
    usage: dict[str, int] = {}
    for key in allowed:
        value = payload.get(key)
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        ):
            usage[key] = value
    return usage


class ModelProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def is_remote(self) -> bool: ...

    @property
    def recipient(self) -> RecipientIdentity: ...

    async def complete(self, request: ModelRequest) -> ModelOutput: ...
