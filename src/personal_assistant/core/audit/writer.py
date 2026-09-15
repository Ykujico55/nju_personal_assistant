from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: str
    actor: str
    resource_type: str
    resource_id: str
    data: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class AuditWriterPort(Protocol):
    async def append(self, event: AuditEvent) -> None: ...

