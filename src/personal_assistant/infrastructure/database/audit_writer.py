"""PostgreSQL append-only audit writer. Redaction runs before insert."""

from __future__ import annotations

from personal_assistant.core.audit import AuditEvent, redact

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
