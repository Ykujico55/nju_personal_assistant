from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class DataClassification(StrEnum):
    PUBLIC = "PUBLIC"
    PERSONAL = "PERSONAL"
    SENSITIVE = "SENSITIVE"
    SECRET = "SECRET"


@dataclass(frozen=True, slots=True)
class ContextField:
    name: str
    value: str
    classification: DataClassification
    source: str


@dataclass(frozen=True, slots=True)
class ModelRequest:
    purpose: str
    instruction: str
    fields: tuple[ContextField, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelOutput:
    text: str
    provider_id: str
    model_id: str
    usage: dict[str, int] = field(default_factory=dict)


class ModelProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def is_remote(self) -> bool: ...

    async def complete(self, request: ModelRequest) -> ModelOutput: ...

