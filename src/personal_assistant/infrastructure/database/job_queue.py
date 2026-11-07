"""PostgreSQL job queue using FOR UPDATE SKIP LOCKED leases."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

import asyncpg

from personal_assistant.core.jobs.management import (
    MAX_REORDER_JOBS,
    TASK_JOB_KINDS,
    reordered,
    validate_limit,
    validate_move,
)
from personal_assistant.core.jobs.queue import Job, JobState, LeaseConflict

from ._support import aware_utc, require_aware
from .connection import PostgresDatabase

_JOB_COLUMNS = (
    "id, kind, payload, idempotency_key, created_at, available_at, state, attempts, "
    "max_attempts, lease_owner, lease_until, result, error_code, version, metadata"
)

# A short shared lock serializes claim/reorder/cancel without holding locks during
# execution. No migration: metadata is already durable and included in backups.
_MANAGEMENT_LOCK = 72489021
_PRIORITY_SQL = """CASE WHEN jsonb_typeof(metadata->'queue_priority') = 'number'
    AND metadata->>'queue_priority' ~ '^[0-9]{1,4}$'
    THEN CASE WHEN (metadata->>'queue_priority')::integer <= 5000
         THEN (metadata->>'queue_priority')::integer ELSE 0 END ELSE 0 END"""
_QUEUE_ORDER = f"{_PRIORITY_SQL} DESC, available_at, created_at, id"


def _row_to_job(row: asyncpg.Record) -> Job:
    payload = row["payload"] if isinstance(row["payload"], dict) else {}
    metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
    result = row["result"] if isinstance(row["result"], dict) else row["result"]
    return Job(
        id=row["id"],
        kind=row["kind"],
        payload=dict(payload),
        idempotency_key=row["idempotency_key"],
        created_at=aware_utc(row["created_at"]),
        available_at=aware_utc(row["available_at"]),
        state=JobState(row["state"]),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        lease_owner=row["lease_owner"],
        lease_until=aware_utc(row["lease_until"]),
        result=result,
        error_code=row["error_code"],
        version=row["version"],
        metadata=dict(metadata),
    )


class PostgresJobQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    async def enqueue(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
        available_at: datetime | None = None,
        max_attempts: int = 5,
    ) -> Job:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if available_at is not None:
            require_aware(available_at, "available_at")
        async with self._db.transaction(), self._db.connection() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO jobs
                    (id, kind, payload, idempotency_key, state, max_attempts, available_at)
                VALUES ($1, $2, $3, $4, 'READY', $5, COALESCE($6, now()))
                ON CONFLICT (kind, idempotency_key) DO NOTHING
                RETURNING *
                """,
                f"job_{uuid4().hex}",
                kind,
                dict(payload),
                idempotency_key,
                max_attempts,
                available_at,
            )
            if row is None:
                existing = await connection.fetchrow(
                    "SELECT * FROM jobs WHERE kind = $1 AND idempotency_key = $2",
                    kind,
                    idempotency_key,
                )
                if existing is None:
                    raise RuntimeError("job enqueue raced with a rollback")
                if existing["payload"] != dict(payload):
                    raise ValueError(
                        "idempotency key was reused with a different job payload"
                    )
                return _row_to_job(existing)
            return _row_to_job(row)

    async def claim(self, *, worker_id: str, lease_seconds: int = 30) -> Job | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        async with self._db.transaction(), self._db.connection() as connection:
            await connection.execute(
                "UPDATE jobs SET state = 'READY', lease_owner = NULL, "
                "lease_until = NULL, updated_at = now() "
                "WHERE state = 'LEASED' AND lease_until <= now()"
            )
            row = await connection.fetchrow(
                f"""
                SELECT * FROM jobs
                 WHERE state = 'READY' AND available_at <= now()
                 ORDER BY {_QUEUE_ORDER}
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
                """
            )
            if row is None:
                return None
            updated = await connection.fetchrow(
                """
                UPDATE jobs SET
                    state = 'LEASED', lease_owner = $2,
                    lease_until = now() + make_interval(secs => $3),
                    attempts = attempts + 1, version = version + 1, updated_at = now()
                 WHERE id = $1
                RETURNING *
                """,
                row["id"],
                worker_id,
                float(lease_seconds),
            )
            return _row_to_job(updated)

    async def heartbeat(
        self, *, job_id: str, worker_id: str, lease_seconds: int = 30
    ) -> Job:
        async with self._db.transaction(), self._db.connection() as connection:
            row = await connection.fetchrow(
                """
                UPDATE jobs SET
                    lease_until = now() + make_interval(secs => $3),
                    version = version + 1, updated_at = now()
                 WHERE id = $1 AND state = 'LEASED' AND lease_owner = $2
                   AND lease_until > now()
                RETURNING *
                """,
                job_id,
                worker_id,
                float(lease_seconds),
            )
            if row is None:
                raise LeaseConflict(f"worker {worker_id!r} does not own job {job_id!r}")
            return _row_to_job(row)

    async def release(self, *, job_id: str, worker_id: str) -> Job:
        async with self._db.transaction(), self._db.connection() as connection:
            row = await connection.fetchrow(
                """
                UPDATE jobs SET
                    state = 'READY', available_at = now(), lease_owner = NULL,
                    lease_until = NULL, version = version + 1, updated_at = now()
                 WHERE id = $1 AND state = 'LEASED' AND lease_owner = $2
                   AND lease_until > now()
                RETURNING *
                """,
                job_id,
                worker_id,
            )
            if row is None:
                raise LeaseConflict(f"worker {worker_id!r} does not own job {job_id!r}")
            return _row_to_job(row)

    async def complete(
        self, *, job_id: str, worker_id: str, result: dict[str, Any]
    ) -> Job:
        async with self._db.transaction(), self._db.connection() as connection:
            row = await connection.fetchrow(
                """
                UPDATE jobs SET
                    state = 'SUCCEEDED', result = $3, lease_owner = NULL,
                    lease_until = NULL, version = version + 1, updated_at = now()
                 WHERE id = $1 AND state = 'LEASED' AND lease_owner = $2
                   AND lease_until > now()
                RETURNING *
                """,
                job_id,
                worker_id,
                dict(result),
            )
            if row is None:
                raise LeaseConflict(f"worker {worker_id!r} does not own job {job_id!r}")
            return _row_to_job(row)

    async def fail(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        retryable: bool,
        retry_at: datetime | None = None,
    ) -> Job:
        async with self._db.transaction(), self._db.connection() as connection:
            row = await connection.fetchrow(
                """
                UPDATE jobs SET
                    state = CASE
                        WHEN $4 AND attempts < max_attempts THEN 'READY'
                        WHEN $4 THEN 'DEAD_LETTER'
                        ELSE 'FAILED'
                    END,
                    available_at = CASE
                        WHEN $4 AND attempts < max_attempts THEN COALESCE($5, now())
                        ELSE available_at
                    END,
                    error_code = $3, lease_owner = NULL, lease_until = NULL,
                    version = version + 1, updated_at = now()
                 WHERE id = $1 AND state = 'LEASED' AND lease_owner = $2
                   AND lease_until > now()
                RETURNING *
                """,
                job_id,
                worker_id,
                error_code,
                retryable,
                retry_at,
            )
            if row is None:
                raise LeaseConflict(f"worker {worker_id!r} does not own job {job_id!r}")
            return _row_to_job(row)

    async def mark_unknown(
        self, *, job_id: str, worker_id: str, diagnostic_code: str
    ) -> Job:
        async with self._db.transaction(), self._db.connection() as connection:
            row = await connection.fetchrow(
                """
                UPDATE jobs SET
                    state = 'WAITING_RECONCILIATION', error_code = $3,
                    lease_owner = NULL, lease_until = NULL,
                    version = version + 1, updated_at = now()
                 WHERE id = $1 AND state = 'LEASED' AND lease_owner = $2
                   AND lease_until > now()
                RETURNING *
                """,
                job_id,
                worker_id,
                diagnostic_code,
            )
            if row is None:
                raise LeaseConflict(f"worker {worker_id!r} does not own job {job_id!r}")
            return _row_to_job(row)

    async def get(self, job_id: str) -> Job | None:
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = $1", job_id
            )
        return _row_to_job(row) if row is not None else None


    async def list_ready_tasks(self, *, limit: int = 100) -> tuple[Job, ...]:
        validate_limit(limit)
        async with self._db.connection() as connection:
            rows = await connection.fetch(
                f"""SELECT * FROM jobs WHERE state = 'READY' AND available_at <= now()
                    AND kind = ANY($1::text[]) AND jsonb_typeof(payload->'task_id') = 'string'
                    AND length(trim(payload->>'task_id')) > 0
                    ORDER BY {_QUEUE_ORDER} LIMIT $2""", list(TASK_JOB_KINDS), limit,
            )
        return tuple(_row_to_job(row) for row in rows)

    async def move_ready(
        self, *, job_id: str, expected_version: int,
        direction: Literal["up", "down", "top", "bottom"],
    ) -> Job:
        validate_move(direction, expected_version)
        async with self._db.transaction(), self._db.connection() as connection:
            await connection.execute("SELECT pg_advisory_xact_lock($1)", _MANAGEMENT_LOCK)
            rows = await connection.fetch(
                f"""SELECT * FROM jobs WHERE state = 'READY' AND available_at <= now()
                    ORDER BY {_QUEUE_ORDER} LIMIT $1 FOR UPDATE""", MAX_REORDER_JOBS + 1,
            )
            jobs = [_row_to_job(row) for row in rows]
            ordered = reordered(jobs, job_id, expected_version, direction)
            selected = next(job for job in ordered if job.id == job_id)
            if [job.id for job in ordered] != [job.id for job in jobs]:
                for index, job in enumerate(ordered):
                    job.metadata["queue_priority"] = len(ordered) - index
                await connection.executemany(
                    """UPDATE jobs SET metadata = $2, version = version + 1,
                        updated_at = now() WHERE id = $1""",
                    [(job.id, job.metadata) for job in ordered],
                )
                selected.version += 1
            return selected

    async def cancel_ready_for_task(self, task_id: str) -> tuple[Job, ...]:
        async with self._db.transaction(), self._db.connection() as connection:
            await connection.execute("SELECT pg_advisory_xact_lock($1)", _MANAGEMENT_LOCK)
            rows = await connection.fetch(
                """UPDATE jobs SET state = 'FAILED', error_code = 'TASK_CANCELLED',
                    lease_owner = NULL, lease_until = NULL, version = version + 1,
                    updated_at = now() WHERE state = 'READY' AND kind = ANY($1::text[])
                    AND payload->>'task_id' = $2 RETURNING *""", list(TASK_JOB_KINDS), task_id,
            )
        return tuple(_row_to_job(row) for row in rows)
