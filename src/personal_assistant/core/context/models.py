from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from personal_assistant.domain import ContextLayer, Sensitivity, TrustLevel, ValidationError


class EvidenceState(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    DELETED = "DELETED"


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    text: str
    source_uri: str
    locator: Mapping[str, Any]
    content_hash: str
    source_version: str
    observed_at: datetime
    producer_extension_id: str
    producer_extension_version: str
    sensitivity: Sensitivity
    trust: TrustLevel
    state: EvidenceState = EvidenceState.CURRENT
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    derived_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        required = (
            self.id,
            self.source_uri,
            self.content_hash,
            self.source_version,
            self.producer_extension_id,
            self.producer_extension_version,
        )
        if any(not value.strip() for value in required):
            raise ValidationError("evidence identity and provenance fields are required")
        if self.observed_at.tzinfo is None:
            raise ValidationError("observed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ContextRequest:
    task_id: str
    purpose: str
    query: str
    max_chars: int = 24_000
    allowed_sensitivity: frozenset[Sensitivity] = field(
        default_factory=lambda: frozenset(
            {Sensitivity.PUBLIC, Sensitivity.PERSONAL, Sensitivity.SENSITIVE}
        )
    )

    def __post_init__(self) -> None:
        if not self.task_id or not self.purpose.strip() or not self.query.strip():
            raise ValidationError("task_id, purpose and query are required")
        if self.max_chars < 1:
            raise ValidationError("max_chars must be positive")
        if Sensitivity.SECRET in self.allowed_sensitivity:
            raise ValidationError("SECRET may not be composed into model context")


@dataclass(frozen=True, slots=True)
class ContextSection:
    layer: ContextLayer
    content: str
    source_ids: tuple[str, ...] = ()
    untrusted_data: bool = False


@dataclass(frozen=True, slots=True)
class ComposedContext:
    sections: tuple[ContextSection, ...]
    evidence: tuple[Evidence, ...]
    omitted_evidence_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def character_count(self) -> int:
        return sum(len(section.content) for section in self.sections)

