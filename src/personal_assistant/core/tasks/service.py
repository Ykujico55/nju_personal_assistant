from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from personal_assistant.core.audit import AuditEvent, AuditWriterPort
from personal_assistant.core.jobs import JobQueuePort
from personal_assistant.core.unit_of_work import UnitOfWorkPort
from personal_assistant.domain import Task, TaskState, ValidationError, utc_now


@dataclass(frozen=True, slots=True)
class TaskMessage:
    id: str
    task_id: str
    actor: str
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TaskEvent:
    sequence: int
    type: str
    task_id: str
    data: dict[str, object]
    occurred_at: datetime


class TaskRepositoryPort(Protocol):
    async def create(self, task: Task, *, idempotency_key: str) -> Task: ...

    async def get(self, task_id: str) -> Task: ...

    async def save(self, task: Task, *, expected_version: int) -> Task: ...

    async def add_message(
        self,
        message: TaskMessage,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> tuple[Task, TaskMessage]: ...

    async def cancel(
        self,
        task_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> Task: ...

    async def messages(self, task_id: str) -> tuple[TaskMessage, ...]: ...


class EventStreamPort(Protocol):
    async def publish(
        self, event_type: str, task_id: str, data: dict[str, object]
    ) -> TaskEvent: ...

    async def after(self, sequence: int) -> tuple[TaskEvent, ...]: ...


class TaskService:
    def __init__(
        self,
        *,
        repository: TaskRepositoryPort,
        queue: JobQueuePort,
        audit: AuditWriterPort,
        events: EventStreamPort,
        unit_of_work: UnitOfWorkPort | None = None,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._audit = audit
        self.events = events
        self._unit_of_work = unit_of_work

    @asynccontextmanager
    async def _scope(self) -> AsyncIterator[None]:
        """Run a command as one durable unit of work when a coordinator exists.

        For the production adapter this makes "create task + initial job +
        QUEUED state + event + audit" a single commit with no recoverable
        half-commit window. The in-memory adapters need no coordinator.
        """

        if self._unit_of_work is None:
            yield
            return
        async with self._unit_of_work.transaction():
            yield

    async def create(
        self, *, objective: str, idempotency_key: str, actor: str = "owner"
    ) -> Task:
        if not objective.strip():
            raise ValidationError("objective is required")
        if not idempotency_key.strip():
            raise ValidationError("Idempotency-Key is required")
        candidate = Task(id=f"task_{uuid4().hex}", objective=objective.strip())
        async with self._scope():
            task = await self._repository.create(
                candidate, idempotency_key=idempotency_key
            )
            if task.state is TaskState.CREATED:
                await self._queue.enqueue(
                    kind="agent.start",
                    payload={"task_id": task.id},
                    idempotency_key=f"task-start:{task.id}",
                )
                queued = Task(
                    id=task.id,
                    objective=task.objective,
                    state=TaskState.QUEUED,
                    version=task.version + 1,
                    created_at=task.created_at,
                )
                task = await self._repository.save(queued, expected_version=task.version)
                await self.events.publish(
                    "task.queued", task.id, {"state": task.state.value}
                )
                await self._audit.append(
                    AuditEvent(
                        "task.created", actor, "task", task.id, {"state": task.state.value}
                    )
                )
        return task

    async def get(self, task_id: str) -> Task:
        return await self._repository.get(task_id)

    async def messages(self, task_id: str) -> tuple[TaskMessage, ...]:
        return await self._repository.messages(task_id)

    async def add_message(
        self,
        *,
        task_id: str,
        content: str,
        expected_version: int,
        actor: str,
        idempotency_key: str,
    ) -> tuple[Task, TaskMessage]:
        if not content.strip():
            raise ValidationError("message content is required")
        message = TaskMessage(
            id=f"msg_{uuid4().hex}",
            task_id=task_id,
            actor=actor,
            content=content.strip(),
            created_at=utc_now(),
        )
        async with self._scope():
            task, message = await self._repository.add_message(
                message,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
            await self.events.publish(
                "task.message_added", task.id, {"message_id": message.id}
            )
        return task, message

    async def cancel(
        self,
        *,
        task_id: str,
        expected_version: int,
        actor: str,
        idempotency_key: str,
    ) -> Task:
        async with self._scope():
            saved = await self._repository.cancel(
                task_id,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
            await self.events.publish(
                "task.cancelled", saved.id, {"state": saved.state.value}
            )
            await self._audit.append(
                AuditEvent("task.cancelled", actor, "task", saved.id)
            )
        return saved
