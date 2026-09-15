from __future__ import annotations

import asyncio
from copy import deepcopy

from personal_assistant.core.audit import AuditEvent, redact


class InMemoryAuditWriter:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = asyncio.Lock()

    async def append(self, event: AuditEvent) -> None:
        safe = AuditEvent(
            event_type=event.event_type,
            actor=event.actor,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            data=redact(event.data),
            occurred_at=event.occurred_at,
        )
        async with self._lock:
            self._events.append(safe)

    async def list_events(self) -> list[AuditEvent]:
        async with self._lock:
            return deepcopy(self._events)

