"""PostgreSQL append-only audit writer. Redaction runs before insert."""

from __future__ import annotations

from typing import Any

from personal_assistant.core.audit import AuditEvent, redact
from personal_assistant.core.audit.usage import MAX_USAGE_CALLS, summarize_usage

from .connection import PostgresDatabase


class PostgresAuditWriter:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    async def append(self, event: AuditEvent) -> None:
        safe_data = redact(event.data)
        async with self._db.connection() as connection:
            await connection.execute(
                "INSERT INTO audit_events "
                "(event_type, actor, resource_type, resource_id, data, occurred_at) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                event.event_type,
                event.actor,
                event.resource_type,
                event.resource_id,
                safe_data,
                event.occurred_at,
            )

    async def model_usage(self, *, limit: int = MAX_USAGE_CALLS) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= MAX_USAGE_CALLS:
            raise ValueError("invalid usage limit")
        async with self._db.connection() as connection:
            rows = await connection.fetch(
                "SELECT data->'usage' AS usage FROM audit_events "
                "WHERE event_type='model.completed' ORDER BY sequence DESC LIMIT $1",
                limit + 1,
            )
        return summarize_usage(rows, limit=limit)
