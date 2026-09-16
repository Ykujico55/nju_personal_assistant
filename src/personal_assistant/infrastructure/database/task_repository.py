"""PostgreSQL task repository and durable event stream."""

from __future__ import annotations

import asyncio

import asyncpg

from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.core.tasks import TaskEvent, TaskMessage
from personal_assistant.domain import (
    AlreadyExistsError,
    ConcurrentModificationError,
    NotFoundError,
    Task,
    TaskState,
    ValidationError,
)

from ._support import aware_utc
from .connection import PostgresDatabase

_TASK_COLUMNS = "id, objective, status, version, created_at"
_MESSAGE_COLUMNS = "id, task_id, actor, content, created_at"


def _row_to_task(row: asyncpg.Record) -> Task:
    return Task(
        id=row["id"],
        objective=row["objective"],
        state=TaskState(row["status"]),
        version=row["version"],
        created_at=aware_utc(row["created_at"]),
    )


def _row_to_message(row: asyncpg.Record) -> TaskMessage:
    return TaskMessage(
        id=row["id"],
        task_id=row["task_id"],
        actor=row["actor"],
        content=row["content"],
        created_at=aware_utc(row["created_at"]),
    )


def _row_to_event(row: asyncpg.Record) -> TaskEvent:
    data = row["data"]
    if not isinstance(data, dict):
        data = {}
    return TaskEvent(
        sequence=row["sequence"],
        type=row["type"],
        task_id=row["task_id"],
        data=data,
        occurred_at=aware_utc(row["occurred_at"]),
    )


class PostgresTaskRepository:
    def __init__(self, database: PostgresDatabase, *, owner_id: str = "owner") -> None:
        self._db = database
        self._owner_id = owner_id

    async def create(self, task: Task, *, idempotency_key: str) -> Task:
        request_sha256 = canonical_sha256({"objective": task.objective})
        async with self._db.transaction(), self._db.connection() as connection:
            inserted = await connection.fetchrow(
                """
                INSERT INTO task_command_idempotency
                    (scope, idempotency_key, request_sha256, task_id)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (scope, idempotency_key) DO NOTHING
                RETURNING task_id
                """,
                "task.create",
                idempotency_key,
                request_sha256,
                task.id,
            )
            if inserted is None:
                replay = await connection.fetchrow(
                    "SELECT request_sha256, task_id FROM task_command_idempotency "
                    "WHERE scope = $1 AND idempotency_key = $2",
                    "task.create",
                    idempotency_key,
                )
                if replay["request_sha256"] != request_sha256:
                    raise ConcurrentModificationError(
                        "idempotency key was reused with a different task objective"
                    )
                row = await connection.fetchrow(
                    f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = $1", replay["task_id"]
                )
                if row is None:
                    raise NotFoundError(f"task not found: {replay['task_id']}")
                return _row_to_task(row)
            try:
                await connection.execute(
                    "INSERT INTO tasks (id, owner_id, objective, status, version) "
                    "VALUES ($1, $2, $3, $4, $5)",
                    task.id,
                    self._owner_id,
                    task.objective,
                    task.state.value,
                    task.version,
                )
            except asyncpg.UniqueViolationError as exc:
                raise AlreadyExistsError(f"task already exists: {task.id}") from exc
            return task

    async def get(self, task_id: str) -> Task:
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = $1", task_id
            )
        if row is None:
            raise NotFoundError(f"task not found: {task_id}")
        return _row_to_task(row)

    async def save(self, task: Task, *, expected_version: int) -> Task:
        async with self._db.transaction(), self._db.connection() as connection:
            row = await connection.fetchrow(
                f"""
                UPDATE tasks
                   SET objective = $2, status = $3, version = $4, updated_at = now()
                 WHERE id = $1 AND version = $5
                RETURNING {_TASK_COLUMNS}
                """,
                task.id,
                task.objective,
                task.state.value,
                expected_version + 1,
                expected_version,
            )
            if row is None:
                exists = await connection.fetchval(
                    "SELECT 1 FROM tasks WHERE id = $1", task.id
                )
                if exists is None:
                    raise NotFoundError(f"task not found: {task.id}")
                raise ConcurrentModificationError(
                    f"task {task.id} expected version {expected_version} was stale"
                )
            return _row_to_task(row)

    async def add_message(
        self,
        message: TaskMessage,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> tuple[Task, TaskMessage]:
        request_sha256 = canonical_sha256(
            {"task_id": message.task_id, "content": message.content}
        )
        async with self._db.transaction(), self._db.connection() as connection:
            inserted = await connection.fetchrow(
                """
                INSERT INTO task_command_idempotency
                    (scope, idempotency_key, request_sha256, task_id, message_id)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (scope, idempotency_key) DO NOTHING
                RETURNING task_id
                """,
                "task.message",
                idempotency_key,
                request_sha256,
                message.task_id,
                message.id,
            )
            if inserted is None:
                replay = await connection.fetchrow(
                    "SELECT request_sha256, task_id, message_id "
                    "FROM task_command_idempotency WHERE scope = $1 AND idempotency_key = $2",
                    "task.message",
                    idempotency_key,
                )
                if replay["request_sha256"] != request_sha256:
                    raise ConcurrentModificationError(
                        "idempotency key was reused with a different message"
                    )
                task_row = await connection.fetchrow(
                    f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = $1", replay["task_id"]
                )
                if task_row is None:
                    raise NotFoundError(f"task not found: {replay['task_id']}")
                message_row = await connection.fetchrow(
                    f"SELECT {_MESSAGE_COLUMNS} FROM task_messages WHERE id = $1",
                    replay["message_id"],
                )
                if message_row is None:
                    raise NotFoundError(f"message not found: {replay['message_id']}")
                return _row_to_task(task_row), _row_to_message(message_row)

            task_row = await connection.fetchrow(
                f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = $1 FOR UPDATE",
                message.task_id,
            )
            if task_row is None:
                raise NotFoundError(f"task not found: {message.task_id}")
            if task_row["version"] != expected_version:
                raise ConcurrentModificationError("task version changed")
            try:
                await connection.execute(
                    "INSERT INTO task_messages (id, task_id, actor, content, created_at) "
                    "VALUES ($1, $2, $3, $4, $5)",
                    message.id,
                    message.task_id,
                    message.actor,
                    message.content,
                    message.created_at,
                )
            except asyncpg.UniqueViolationError as exc:
                raise AlreadyExistsError(f"message already exists: {message.id}") from exc
            updated = await connection.fetchrow(
                f"""
                UPDATE tasks SET version = version + 1, updated_at = now()
                 WHERE id = $1 AND version = $2
                RETURNING {_TASK_COLUMNS}
                """,
                message.task_id,
                expected_version,
            )
            if updated is None:
                raise ConcurrentModificationError("task version changed")
            return _row_to_task(updated), message

    async def cancel(
        self,
        task_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> Task:
        request_sha256 = canonical_sha256({"task_id": task_id})
        async with self._db.transaction(), self._db.connection() as connection:
            inserted = await connection.fetchrow(
                """
                INSERT INTO task_command_idempotency
                    (scope, idempotency_key, request_sha256, task_id)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (scope, idempotency_key) DO NOTHING
                RETURNING task_id
                """,
                "task.cancel",
                idempotency_key,
                request_sha256,
                task_id,
            )
            if inserted is None:
                replay = await connection.fetchrow(
                    "SELECT request_sha256, task_id FROM task_command_idempotency "
                    "WHERE scope = $1 AND idempotency_key = $2",
                    "task.cancel",
                    idempotency_key,
                )
                if replay["request_sha256"] != request_sha256:
                    raise ConcurrentModificationError(
                        "idempotency key was reused for a different task cancellation"
                    )
                row = await connection.fetchrow(
                    f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = $1", replay["task_id"]
                )
                if row is None:
                    raise NotFoundError(f"task not found: {replay['task_id']}")
                return _row_to_task(row)

            task_row = await connection.fetchrow(
                f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = $1 FOR UPDATE", task_id
            )
            if task_row is None:
                raise NotFoundError(f"task not found: {task_id}")
            if task_row["version"] != expected_version:
                raise ConcurrentModificationError("task version changed")
            if TaskState(task_row["status"]).terminal:
                raise ValidationError("terminal task cannot be cancelled")
            updated = await connection.fetchrow(
                f"""
                UPDATE tasks SET status = $2, version = version + 1, updated_at = now()
                 WHERE id = $1 AND version = $3
                RETURNING {_TASK_COLUMNS}
                """,
                task_id,
                TaskState.CANCELLED.value,
                expected_version,
            )
            if updated is None:
                raise ConcurrentModificationError("task version changed")
            return _row_to_task(updated)

    async def messages(self, task_id: str) -> tuple[TaskMessage, ...]:
        async with self._db.connection() as connection:
            exists = await connection.fetchval(
                "SELECT 1 FROM tasks WHERE id = $1", task_id
            )
            if exists is None:
                raise NotFoundError(f"task not found: {task_id}")
            rows = await connection.fetch(
                f"SELECT {_MESSAGE_COLUMNS} FROM task_messages "
                "WHERE task_id = $1 ORDER BY created_at, id",
                task_id,
            )
        return tuple(_row_to_message(row) for row in rows)


class PostgresEventStream:
    """Durable event log. NOTIFY only wakes readers; the table is the truth."""

    _CHANNEL = "pa_task_events"

    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database
        self._listener: asyncpg.Connection | None = None
        self._wake = asyncio.Event()
        self._listener_lock = asyncio.Lock()

    async def publish(
        self, event_type: str, task_id: str, data: dict[str, object]
    ) -> TaskEvent:
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                "INSERT INTO task_events (type, task_id, data) VALUES ($1, $2, $3) "
                "RETURNING sequence, type, task_id, data, occurred_at",
                event_type,
                task_id,
                dict(data),
            )
            await connection.execute(
                "SELECT pg_notify($1, $2)", self._CHANNEL, str(row["sequence"])
            )
        return _row_to_event(row)

    async def after(self, sequence: int) -> tuple[TaskEvent, ...]:
        async with self._db.connection() as connection:
            rows = await connection.fetch(
                "SELECT sequence, type, task_id, data, occurred_at FROM task_events "
                "WHERE sequence > $1 ORDER BY sequence",
                sequence,
            )
        return tuple(_row_to_event(row) for row in rows)

    async def wait_after(
        self, sequence: int, timeout_seconds: float = 15.0
    ) -> tuple[TaskEvent, ...]:
        await self._ensure_listener()
        self._wake.clear()
        existing = await self.after(sequence)
        if existing:
            return existing
        try:
            await asyncio.wait_for(self._wake.wait(), timeout_seconds)
        except TimeoutError:
            return ()
        return await self.after(sequence)

    async def close(self) -> None:
        self._listener = None
        self._wake.clear()

    async def _ensure_listener(self) -> None:
        if self._listener is not None:
            return
        async with self._listener_lock:
            if self._listener is not None:
                return
            listener = await self._db.new_extra_connection()
            await listener.add_listener(self._CHANNEL, self._on_notify)
            self._listener = listener

    def _on_notify(self, *_args: object) -> None:
        self._wake.set()
