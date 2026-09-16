"""PostgreSQL run, checkpoint and observation stores."""

from __future__ import annotations

import asyncpg

from personal_assistant.core.agent.checkpoint import Observation
from personal_assistant.domain import (
    AlreadyExistsError,
    ConcurrentModificationError,
    NotFoundError,
    TaskRun,
    TaskState,
)

from ._support import aware_utc
from .connection import PostgresDatabase

_RUN_COLUMNS = (
    "id, task_id, objective, state, version, segment_number, active_started_at, "
    "updated_at, consecutive_errors, no_progress_rounds, same_call_without_progress, "
    "last_call_fingerprint, last_observation_fingerprint, waiting_reference, pause_reason"
)


def _row_to_run(row: asyncpg.Record) -> TaskRun:
    objective = row["objective"]
    return TaskRun(
        id=row["id"],
        task_id=row["task_id"],
        objective=objective if objective is not None else "",
        state=TaskState(row["state"]),
        version=row["version"],
        segment_number=row["segment_number"] or 1,
        active_started_at=aware_utc(row["active_started_at"]) or aware_utc(row["updated_at"]),
        updated_at=aware_utc(row["updated_at"]),
        consecutive_errors=row["consecutive_errors"],
        no_progress_rounds=row["no_progress_rounds"],
        same_call_without_progress=row["same_call_without_progress"],
        last_call_fingerprint=row["last_call_fingerprint"],
        last_observation_fingerprint=row["last_observation_fingerprint"],
        waiting_reference=row["waiting_reference"],
        pause_reason=row["pause_reason"],
    )


class PostgresRunRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    async def add(self, run: TaskRun) -> None:
        async with self._db.transaction(), self._db.connection() as connection:
            try:
                await connection.execute(
                    """
                    INSERT INTO agent_runs (
                        id, task_id, state, objective, segment_number, active_started_at,
                        started_at, updated_at, consecutive_errors, no_progress_rounds,
                        same_call_without_progress, last_call_fingerprint,
                        last_observation_fingerprint, waiting_reference, pause_reason, version
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15
                    )
                    """,
                    run.id,
                    run.task_id,
                    run.state.value,
                    run.objective,
                    run.segment_number,
                    run.active_started_at,
                    run.updated_at,
                    run.consecutive_errors,
                    run.no_progress_rounds,
                    run.same_call_without_progress,
                    run.last_call_fingerprint,
                    run.last_observation_fingerprint,
                    run.waiting_reference,
                    run.pause_reason,
                    run.version,
                )
            except asyncpg.ForeignKeyViolationError as exc:
                raise NotFoundError(f"task not found: {run.task_id}") from exc
            except asyncpg.UniqueViolationError as exc:
                raise AlreadyExistsError(f"run already exists: {run.id}") from exc

    async def get(self, run_id: str) -> TaskRun:
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_RUN_COLUMNS} FROM agent_runs WHERE id = $1", run_id
            )
        if row is None:
            raise NotFoundError(f"run not found: {run_id}")
        return _row_to_run(row)

    async def save(self, run: TaskRun, *, expected_version: int) -> None:
        async with self._db.transaction(), self._db.connection() as connection:
            row = await connection.fetchrow(
                """
                UPDATE agent_runs SET
                    state = $2, objective = $3, segment_number = $4,
                    active_started_at = $5, updated_at = $6, consecutive_errors = $7,
                    no_progress_rounds = $8, same_call_without_progress = $9,
                    last_call_fingerprint = $10, last_observation_fingerprint = $11,
                    waiting_reference = $12, pause_reason = $13, version = $14
                 WHERE id = $1 AND version = $15
                RETURNING id
                """,
                run.id,
                run.state.value,
                run.objective,
                run.segment_number,
                run.active_started_at,
                run.updated_at,
                run.consecutive_errors,
                run.no_progress_rounds,
                run.same_call_without_progress,
                run.last_call_fingerprint,
                run.last_observation_fingerprint,
                run.waiting_reference,
                run.pause_reason,
                expected_version + 1,
                expected_version,
            )
            if row is None:
                exists = await connection.fetchval(
                    "SELECT 1 FROM agent_runs WHERE id = $1", run.id
                )
                if exists is None:
                    raise NotFoundError(f"run not found: {run.id}")
                raise ConcurrentModificationError(
                    f"run {run.id} expected version {expected_version} was stale"
                )


class PostgresCheckpointStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    async def load(self, run_id: str) -> dict[str, object]:
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                "SELECT snapshot FROM run_checkpoints WHERE run_id = $1 "
                "ORDER BY sequence DESC LIMIT 1",
                run_id,
            )
        if row is None or not isinstance(row["snapshot"], dict):
            return {}
        return dict(row["snapshot"])

    async def save(self, run: TaskRun, snapshot: dict[str, object]) -> None:
        payload = {**snapshot, "run_version": run.version}
        async with self._db.transaction(), self._db.connection() as connection:
            await connection.execute(
                """
                INSERT INTO run_checkpoints (run_id, sequence, state, snapshot, run_version)
                VALUES ($1, nextval('run_checkpoint_sequence'), $2, $3, $4)
                """,
                run.id,
                run.state.value,
                payload,
                run.version,
            )


class PostgresObservationStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    async def append(self, observation: Observation) -> None:
        async with self._db.transaction(), self._db.connection() as connection:
            await connection.execute(
                "INSERT INTO run_observations (run_id, value) VALUES ($1, $2)",
                observation.run_id,
                observation.value,
            )
