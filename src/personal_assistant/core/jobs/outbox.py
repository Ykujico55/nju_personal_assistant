"""Durable intent contract for effectively-once external actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class SideEffectState(StrEnum):
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SideEffectIntent:
    id: str
    task_id: str
    tool_id: str
    idempotency_key: str
    approval_id: str
    canonical_payload_sha256: str
    state: SideEffectState
    created_at: datetime
    receipt: dict[str, Any] = field(default_factory=dict)


class SideEffectOutboxPort(Protocol):
    async def create_with_approval_consumption(self, intent: SideEffectIntent) -> None:
        """Persist intent and consume approval in one database transaction."""

    async def mark_succeeded(self, intent_id: str, receipt: dict[str, Any]) -> None: ...

    async def mark_failed(self, intent_id: str, error_code: str) -> None: ...

    async def mark_unknown(self, intent_id: str, diagnostic_code: str) -> None: ...

