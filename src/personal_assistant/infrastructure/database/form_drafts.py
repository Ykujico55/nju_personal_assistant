"""Atomic PostgreSQL CAS and idempotent receipt store for task form drafts."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from personal_assistant.core.tasks.form_drafts import FormDraft
from personal_assistant.domain import ConcurrentModificationError, NotFoundError

from ._support import aware_utc
from .connection import PostgresDatabase

_COLUMNS = (
    "task_id, extension_id, extension_version, form_id, json_schema, "
    "ui_schema, values_json, sources, version, updated_at"
)


def _from_row(row: Any) -> FormDraft:
    return FormDraft(
        task_id=row["task_id"],
        extension_id=row["extension_id"],
        extension_version=row["extension_version"],
        form_id=row["form_id"],
        json_schema=row["json_schema"],
        ui_schema=row["ui_schema"],
        values=row["values_json"],
        sources=row["sources"],
        version=row["version"],
        updated_at=aware_utc(row["updated_at"]),
    )


def _receipt(draft: FormDraft) -> dict[str, Any]:
    return {**asdict(draft), "updated_at": draft.updated_at.isoformat()}


def _from_receipt(data: dict[str, Any]) -> FormDraft:
    return FormDraft(**{**data, "updated_at": datetime.fromisoformat(data["updated_at"])})


class PostgresFormDraftStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    async def get(self, task_id: str) -> FormDraft:
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_COLUMNS} FROM task_form_drafts WHERE task_id = $1", task_id
            )
        if row is None:
            raise NotFoundError(f"form draft not found for task {task_id}")
        return _from_row(row)

    async def command_receipt(self, key: str) -> tuple[str, FormDraft] | None:
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                "SELECT request_sha256, receipt FROM task_form_draft_commands "
                "WHERE idempotency_key = $1", key
            )
        return (row["request_sha256"], _from_receipt(row["receipt"])) if row is not None else None

    async def _claim(self, connection: Any, key: str, fingerprint: str) -> FormDraft | None:
        inserted = await connection.fetchval(
            "INSERT INTO task_form_draft_commands "
            "(idempotency_key, request_sha256, receipt) VALUES ($1, $2, '{}'::jsonb) "
            "ON CONFLICT (idempotency_key) DO NOTHING RETURNING idempotency_key",
            key,
            fingerprint,
        )
        if inserted is not None:
            return None
        prior = await connection.fetchrow(
            "SELECT request_sha256, receipt FROM task_form_draft_commands "
            "WHERE idempotency_key = $1",
            key,
        )
        if prior["request_sha256"] != fingerprint:
            raise ConcurrentModificationError("draft idempotency key was reused with new content")
        return _from_receipt(prior["receipt"])

    async def _complete(self, connection: Any, key: str, draft: FormDraft) -> None:
        await connection.execute(
            "UPDATE task_form_draft_commands SET receipt = $2 WHERE idempotency_key = $1",
            key,
            _receipt(draft),
        )

    async def create(self, draft: FormDraft, *, key: str, fingerprint: str) -> FormDraft:
        async with self._db.transaction(), self._db.connection() as connection:
            replay = await self._claim(connection, key, fingerprint)
            if replay is not None:
                return replay
            # Lock the parent row: competing creates for the same task serialize.
            task = await connection.fetchval(
                "SELECT id FROM tasks WHERE id = $1 FOR UPDATE", draft.task_id
            )
            if task is None:
                raise NotFoundError(f"task not found: {draft.task_id}")
            existing = await connection.fetchval(
                "SELECT task_id FROM task_form_drafts WHERE task_id = $1", draft.task_id
            )
            if existing is not None:
                raise ConcurrentModificationError("task already has a form draft")
            row = await connection.fetchrow(
                f"INSERT INTO task_form_drafts ({_COLUMNS}) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) "
                f"RETURNING {_COLUMNS}",
                draft.task_id,
                draft.extension_id,
                draft.extension_version,
                draft.form_id,
                draft.json_schema,
                draft.ui_schema,
                draft.values,
                draft.sources,
                draft.version,
                draft.updated_at,
            )
            saved = _from_row(row)
            await self._complete(connection, key, saved)
            return saved

    async def replace(
        self, task_id: str, values: dict[str, Any], *, version: int, key: str, fingerprint: str
    ) -> FormDraft:
        async with self._db.transaction(), self._db.connection() as connection:
            replay = await self._claim(connection, key, fingerprint)
            if replay is not None:
                return replay
            row = await connection.fetchrow(
                f"SELECT {_COLUMNS} FROM task_form_drafts WHERE task_id = $1 FOR UPDATE", task_id
            )
            if row is None:
                raise NotFoundError(f"form draft not found for task {task_id}")
            current = _from_row(row)
            if current.version != version:
                raise ConcurrentModificationError("form draft version changed")
            sources = {
                name: (
                    current.sources.get(name, "UNKNOWN")
                    if (
                        name in values
                        and name in current.values
                        and current.values[name] == values[name]
                    )
                    else "USER_INPUT" if name in values else "UNKNOWN"
                )
                for name in current.json_schema["properties"]
            }
            updated = await connection.fetchrow(
                f"UPDATE task_form_drafts SET values_json = $2, sources = $3, "
                "version = version + 1, updated_at = now() WHERE task_id = $1 "
                f"RETURNING {_COLUMNS}",
                task_id,
                values,
                sources,
            )
            saved = _from_row(updated)
            await self._complete(connection, key, saved)
            return saved
