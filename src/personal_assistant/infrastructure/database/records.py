"""Owner-scoped CAS records; command mutations and original receipts commit together."""

from __future__ import annotations

from typing import Any

from personal_assistant.core.records import Command, fingerprint
from personal_assistant.domain import ConcurrentModificationError, ValidationError

from .connection import PostgresDatabase


class PostgresRecordStore:
    def __init__(self, database: PostgresDatabase, *, owner_id: str) -> None:
        self.database = database
        self._owner = owner_id

    async def get(self, bucket: str, key: str) -> dict[str, Any] | None:
        async with self.database.connection() as con:
            row = await con.fetchrow(
                "SELECT record_id AS id, version, value FROM runtime_records "
                "WHERE owner_id=$1 AND bucket=$2 AND record_id=$3",
                self._owner,
                bucket,
                key,
            )
        return dict(row) if row else None

    async def list(self, bucket: str) -> tuple[dict[str, Any], ...]:
        async with self.database.connection() as con:
            rows = await con.fetch(
                "SELECT record_id AS id, version, value FROM runtime_records "
                "WHERE owner_id=$1 AND bucket=$2 ORDER BY record_id",
                self._owner,
                bucket,
            )
        return tuple(dict(row) for row in rows)

    async def put(
        self, bucket: str, key: str, value: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]:
        fingerprint(value)
        async with self.database.connection() as con:
            if expected_version == 0:
                row = await con.fetchrow(
                    "INSERT INTO runtime_records "
                    "(owner_id,bucket,record_id,value,version) VALUES ($1,$2,$3,$4,1) "
                    "ON CONFLICT DO NOTHING RETURNING record_id AS id,version,value",
                    self._owner,
                    bucket,
                    key,
                    value,
                )
            else:
                row = await con.fetchrow(
                    "UPDATE runtime_records SET value=$4, "
                    "version=version+1,updated_at=now() WHERE owner_id=$1 AND bucket=$2 "
                    "AND record_id=$3 AND version=$5 RETURNING record_id AS id,version,value",
                    self._owner,
                    bucket,
                    key,
                    value,
                    expected_version,
                )
            if row is None:
                raise ConcurrentModificationError("record version changed")
            return dict(row)

    async def command(self, key: str, request: object, action: Command) -> dict[str, Any]:
        if not key or len(key) > 256:
            raise ValidationError("a bounded command key is required")
        digest = fingerprint(request)
        async with self.database.transaction(), self.database.connection() as con:
            await con.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                self._owner + ":command:" + key,
            )
            row = await con.fetchrow(
                "SELECT fingerprint,response FROM runtime_commands "
                "WHERE owner_id=$1 AND command_key=$2",
                self._owner,
                key,
            )
            if row:
                if row["fingerprint"] != digest:
                    raise ConcurrentModificationError("command key belongs to another request")
                return dict(row["response"])
            result = await action()
            await con.execute(
                "INSERT INTO runtime_commands "
                "(owner_id,command_key,fingerprint,response) VALUES ($1,$2,$3,$4)",
                self._owner,
                key,
                digest,
                result,
            )
            return result
