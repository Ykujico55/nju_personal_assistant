"""PostgreSQL stores for the supervised browser boundary.

All transitions are single atomic statements with explicit state guards, so a
crashed ``EXECUTING`` row can never be re-clicked and a concurrent writer can
never overwrite a newer session version.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import asyncpg

from personal_assistant.core.browser import (
    BrowserAdapterRecord,
    BrowserSessionRecord,
    BrowserSessionState,
)
from personal_assistant.domain.errors import (
    AlreadyExistsError,
    ConcurrentModificationError,
    NotFoundError,
)

from .connection import PostgresDatabase

_SESSION_COLUMNS = (
    "session_id, task_id, extension_id, extension_version, purpose, state, origin, url, "
    "adapter_id, adapter_version, app_id, transaction_id, page_fingerprint, preview_hash, "
    "preview_nonce, preview, outcome, receipt, receipt_baseline, diagnostic_code, "
    "re_navigations, visited_paths, fill_count, submit_count, apps_count, owner_id, "
    "version, created_at, updated_at, expires_at"
)

_INSERT_SESSION_SQL = (
    f"INSERT INTO browser_sessions ({_SESSION_COLUMNS}) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, "
    "$16::jsonb, $17, $18::jsonb, $19, $20, $21, $22, $23, $24, $25, $26, 0, $27, $28, $29) "
    "RETURNING version"
)

_SAVE_SESSION_SQL = (
    "UPDATE browser_sessions SET state = $2, origin = $3, url = $4, adapter_id = $5, "
    "adapter_version = $6, app_id = $7, transaction_id = $8, page_fingerprint = $9, "
    "preview_hash = $10, preview_nonce = $11, preview = $12::jsonb, outcome = $13, "
    "receipt = $14::jsonb, receipt_baseline = $15, diagnostic_code = $16, "
    "re_navigations = $17, visited_paths = $18, fill_count = $19, submit_count = $20, "
    "apps_count = $21, owner_id = $22, updated_at = $23, expires_at = $24, "
    "version = version + 1 "
    "WHERE session_id = $1 AND version = $25 "
    "RETURNING version"
)

_RECOVER_SQL = (
    "UPDATE browser_sessions SET state = 'UNKNOWN', outcome = 'UNKNOWN', "
    "diagnostic_code = 'EXECUTION_INTERRUPTED', updated_at = now(), version = version + 1 "
    "WHERE state = 'EXECUTING' "
    "AND (owner_id = '' OR NOT (owner_id = ANY($1::text[]))) "
    "RETURNING session_id"
)

_UPSERT_ADAPTER_SQL = (
    "INSERT INTO browser_adapters (extension_id, adapter_id, adapter_version, "
    "extension_version, descriptor, version, created_at, updated_at) "
    "VALUES ($1, $2, $3, $4, $5::jsonb, 0, $6, $7) "
    "ON CONFLICT (extension_id, adapter_id, adapter_version) DO UPDATE SET "
    "extension_version = EXCLUDED.extension_version, descriptor = EXCLUDED.descriptor, "
    "updated_at = EXCLUDED.updated_at, version = browser_adapters.version + 1 "
    "RETURNING version, created_at"
)


def _json_or_none(value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _to_session(row: Mapping[str, Any]) -> BrowserSessionRecord:
    preview = row.get("preview")
    if isinstance(preview, str):
        preview = json.loads(preview)
    receipt = row.get("receipt")
    if isinstance(receipt, str):
        receipt = json.loads(receipt)
    return BrowserSessionRecord(
        session_id=str(row["session_id"]),
        task_id=str(row["task_id"]),
        extension_id=str(row["extension_id"]),
        extension_version=str(row["extension_version"]),
        purpose=str(row["purpose"]),
        state=BrowserSessionState(str(row["state"])),
        origin=str(row["origin"]),
        url=str(row["url"]),
        adapter_id=str(row["adapter_id"]),
        adapter_version=str(row["adapter_version"]),
        app_id=str(row["app_id"]),
        transaction_id=str(row["transaction_id"]),
        page_fingerprint=str(row["page_fingerprint"]) if row.get("page_fingerprint") else "",
        preview_hash=str(row["preview_hash"]) if row.get("preview_hash") else "",
        preview_nonce=str(row["preview_nonce"]) if row.get("preview_nonce") else "",
        preview=dict(preview) if isinstance(preview, Mapping) else None,
        outcome=str(row["outcome"]),
        receipt=dict(receipt) if isinstance(receipt, Mapping) else None,
        receipt_baseline=str(row.get("receipt_baseline") or ""),
        diagnostic_code=str(row["diagnostic_code"]),
        re_navigations=int(row["re_navigations"]),
        visited_paths=tuple(str(item) for item in row.get("visited_paths", ()) or ()),
        fill_count=int(row["fill_count"]),
        submit_count=int(row["submit_count"]),
        apps_count=int(row["apps_count"]),
        owner_id=str(row["owner_id"]),
        version=int(row["version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
    )


def _to_adapter(row: Mapping[str, Any]) -> BrowserAdapterRecord:
    descriptor = row["descriptor"]
    if isinstance(descriptor, str):
        descriptor = json.loads(descriptor)
    return BrowserAdapterRecord(
        extension_id=str(row["extension_id"]),
        extension_version=str(row["extension_version"]),
        adapter_id=str(row["adapter_id"]),
        adapter_version=str(row["adapter_version"]),
        descriptor=dict(descriptor) if isinstance(descriptor, Mapping) else {},
        version=int(row["version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresBrowserSessionStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def create(self, record: BrowserSessionRecord) -> BrowserSessionRecord:
        async with self._database.connection() as connection:
            existing = await connection.fetchrow(
                "SELECT session_id FROM browser_sessions WHERE session_id = $1",
                record.session_id,
            )
            if existing is not None:
                raise AlreadyExistsError(f"browser session {record.session_id} already exists")
            try:
                row = await connection.fetchrow(
                    _INSERT_SESSION_SQL,
                    record.session_id,
                    record.task_id,
                    record.extension_id,
                    record.extension_version,
                    record.purpose,
                    record.state.value,
                    record.origin,
                    record.url,
                    record.adapter_id,
                    record.adapter_version,
                    record.app_id,
                    record.transaction_id,
                    record.page_fingerprint or None,
                    record.preview_hash or None,
                    record.preview_nonce or None,
                    _json_or_none(record.preview),
                    record.outcome,
                    _json_or_none(record.receipt),
                    record.receipt_baseline,
                    record.diagnostic_code,
                    record.re_navigations,
                    list(record.visited_paths),
                    record.fill_count,
                    record.submit_count,
                    record.apps_count,
                    record.owner_id,
                    record.created_at,
                    record.updated_at,
                    record.expires_at,
                )
            except asyncpg.UniqueViolationError as exc:
                if getattr(exc, "constraint_name", "") == ("browser_sessions_task_open_unique_idx"):
                    raise AlreadyExistsError(
                        f"an open browser session already exists for task {record.task_id}"
                    ) from exc
                raise AlreadyExistsError(
                    f"browser session {record.session_id} already exists"
                ) from exc
        if row is None:  # pragma: no cover - INSERT ... RETURNING always yields a row
            raise AlreadyExistsError(f"browser session {record.session_id} already exists")
        return replace(record, version=0)

    async def get(self, session_id: str) -> BrowserSessionRecord | None:
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_SESSION_COLUMNS} FROM browser_sessions WHERE session_id = $1",
                session_id,
            )
        if row is None:
            return None
        return _to_session(dict(row))

    async def save(
        self, record: BrowserSessionRecord, *, expected_version: int
    ) -> BrowserSessionRecord:
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                _SAVE_SESSION_SQL,
                record.session_id,
                record.state.value,
                record.origin,
                record.url,
                record.adapter_id,
                record.adapter_version,
                record.app_id,
                record.transaction_id,
                record.page_fingerprint or None,
                record.preview_hash or None,
                record.preview_nonce or None,
                _json_or_none(record.preview),
                record.outcome,
                _json_or_none(record.receipt),
                record.receipt_baseline,
                record.diagnostic_code,
                record.re_navigations,
                list(record.visited_paths),
                record.fill_count,
                record.submit_count,
                record.apps_count,
                record.owner_id,
                record.updated_at,
                record.expires_at,
                expected_version,
            )
            if row is None:
                existing = await connection.fetchrow(
                    "SELECT version FROM browser_sessions WHERE session_id = $1",
                    record.session_id,
                )
                if existing is None:
                    raise NotFoundError(f"browser session {record.session_id} not found")
                raise ConcurrentModificationError(
                    "the browser session was modified by another writer"
                )
        return replace(record, version=expected_version + 1)

    async def find_active_for_task(self, task_id: str) -> tuple[BrowserSessionRecord, ...]:
        async with self._database.connection() as connection:
            rows = await connection.fetch(
                f"SELECT {_SESSION_COLUMNS} FROM browser_sessions "
                "WHERE task_id = $1 ORDER BY created_at, session_id",
                task_id,
            )
        return tuple(_to_session(dict(row)) for row in rows)

    async def recover_stale_executions(
        self, *, active_owners: Sequence[str] = ()
    ) -> tuple[str, ...]:
        async with self._database.connection() as connection:
            rows = await connection.fetch(_RECOVER_SQL, list(active_owners))
        return tuple(str(row["session_id"]) for row in rows)

    async def close(self) -> None:
        return None


class PostgresBrowserAdapterStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def upsert(self, record: BrowserAdapterRecord) -> BrowserAdapterRecord:
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                _UPSERT_ADAPTER_SQL,
                record.extension_id,
                record.adapter_id,
                record.adapter_version,
                record.extension_version,
                _json_or_none(record.descriptor),
                record.created_at,
                record.updated_at,
            )
        if row is None:  # pragma: no cover - UPSERT ... RETURNING always yields a row
            return record
        return replace(
            record,
            version=int(row["version"]),
            created_at=row["created_at"],
            updated_at=record.updated_at,
        )

    async def get(
        self, extension_id: str, adapter_id: str, adapter_version: str
    ) -> BrowserAdapterRecord | None:
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                "SELECT extension_id, adapter_id, adapter_version, extension_version, "
                "descriptor, version, created_at, updated_at FROM browser_adapters "
                "WHERE extension_id = $1 AND adapter_id = $2 AND adapter_version = $3",
                extension_id,
                adapter_id,
                adapter_version,
            )
        if row is None:
            return None
        return _to_adapter(dict(row))

    async def list_for_extension(self, extension_id: str) -> tuple[BrowserAdapterRecord, ...]:
        async with self._database.connection() as connection:
            rows = await connection.fetch(
                "SELECT extension_id, adapter_id, adapter_version, extension_version, "
                "descriptor, version, created_at, updated_at FROM browser_adapters "
                "WHERE extension_id = $1 ORDER BY adapter_id, adapter_version",
                extension_id,
            )
        return tuple(_to_adapter(dict(row)) for row in rows)

    async def close(self) -> None:
        return None


__all__ = ["PostgresBrowserAdapterStore", "PostgresBrowserSessionStore"]
