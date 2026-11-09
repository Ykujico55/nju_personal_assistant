"""Lease-fenced data view for one index build, using the public SQL broker only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from personal_assistant_sdk import HostDataClient
from personal_assistant_sdk.models import JsonValue

_GUARD = (
    "SELECT 1 / COALESCE((SELECT 1 FROM knowledge_meta "
    "WHERE key = 'index.build.lease.v1' AND value->>'owner' = $1 "
    "AND value->>'state' = 'RUNNING' "
    "AND (value->>'expires_at')::double precision > extract(epoch from clock_timestamp()) "
    "FOR UPDATE), 0) AS lease_guard"
)


class LeaseDataClient:
    """Every read/write batch holds the lease row lock through commit.

    Both guards run in the same transaction as the original statements. Missing,
    expired or replaced ownership raises an SQL error and rolls back the batch.
    No extension-specific operation is added to the host.
    """

    def __init__(self, data: HostDataClient, owner: str) -> None:
        self._data, self._owner = data, owner

    async def execute(
        self, statement: str, parameters: Sequence[JsonValue] = (), *, timeout_seconds: float = 30
    ) -> Mapping[str, JsonValue]:
        result = await self.transaction(
            [{"statement": statement, "parameters": list(parameters)}],
            timeout_seconds=timeout_seconds,
        )
        return cast(Mapping[str, JsonValue], cast(list[Any], result["results"])[0])

    async def transaction(
        self, statements: Sequence[Mapping[str, JsonValue]], *, timeout_seconds: float = 60
    ) -> Mapping[str, JsonValue]:
        guard: Mapping[str, JsonValue] = {"statement": _GUARD, "parameters": [self._owner]}
        result = await self._data.transaction(
            [guard, *statements, guard], timeout_seconds=timeout_seconds
        )
        rows = result.get("results")
        if not isinstance(rows, list) or len(rows) != len(statements) + 2:
            raise ValueError("INDEX_LEASE_PROTOCOL_ERROR")
        return {"results": rows[1:-1]}

    async def migrate(
        self, migrations: Sequence[Mapping[str, JsonValue]], *, timeout_seconds: float = 60
    ) -> Mapping[str, JsonValue]:
        # Schema preparation must precede acquiring this build lease.
        raise ValueError("INDEX_LEASE_MIGRATION_INVALID")

    async def aclose(self) -> None:
        # This is a scoped view; the caller owns the underlying broker.
        return None
