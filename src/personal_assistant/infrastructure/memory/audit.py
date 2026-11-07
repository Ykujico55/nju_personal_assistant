from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from personal_assistant.core.audit import AuditEvent, redact
from personal_assistant.core.audit.usage import MAX_USAGE_CALLS, summarize_usage


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


    async def model_usage(self, *, limit: int = MAX_USAGE_CALLS) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= MAX_USAGE_CALLS:
            raise ValueError("invalid usage limit")
        async with self._lock:
            rows = [
                {"usage": event.data.get("usage", {})}
                for event in reversed(self._events)
                if event.event_type == "model.completed"
            ][:limit + 1]
        return summarize_usage(rows, limit=limit)
