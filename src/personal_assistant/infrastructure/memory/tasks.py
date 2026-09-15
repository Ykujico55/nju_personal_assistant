from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace

from personal_assistant.core.tasks import TaskEvent, TaskMessage
from personal_assistant.domain import (
    AlreadyExistsError,
    ConcurrentModificationError,
    NotFoundError,
    Task,
    TaskState,
    ValidationError,
    utc_now,
)


class InMemoryTaskRepository:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._messages: dict[str, list[TaskMessage]] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._message_commands: dict[str, tuple[str, str, str]] = {}
        self._cancel_commands: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create(self, task: Task, *, idempotency_key: str) -> Task:
        async with self._lock:
            replay = self._idempotency.get(idempotency_key)
            if replay is not None:
                existing_id, original_objective = replay
                if original_objective != task.objective:
                    raise ConcurrentModificationError(
                        "idempotency key was reused with a different task objective"
                    )
                return deepcopy(self._tasks[existing_id])
            if task.id in self._tasks:
                raise AlreadyExistsError(f"task already exists: {task.id}")
            self._tasks[task.id] = deepcopy(task)
            self._messages[task.id] = []
            self._idempotency[idempotency_key] = (task.id, task.objective)
            return deepcopy(task)

    async def get(self, task_id: str) -> Task:
        async with self._lock:
            try:
                return deepcopy(self._tasks[task_id])
            except KeyError as exc:
                raise NotFoundError(f"task not found: {task_id}") from exc

    async def save(self, task: Task, *, expected_version: int) -> Task:
        async with self._lock:
            current = self._tasks.get(task.id)
            if current is None:
                raise NotFoundError(f"task not found: {task.id}")
            if current.version != expected_version or task.version <= expected_version:
                raise ConcurrentModificationError(
                    f"task {task.id} expected version {expected_version}, found {current.version}"
                )
            self._tasks[task.id] = deepcopy(task)
            return deepcopy(task)

    async def add_message(
        self,
        message: TaskMessage,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> tuple[Task, TaskMessage]:
        async with self._lock:
            replay = self._message_commands.get(idempotency_key)
            if replay is not None:
                replay_task, replay_message, original_content = replay
                if replay_task != message.task_id or original_content != message.content:
                    raise ConcurrentModificationError(
                        "idempotency key was reused with a different message"
                    )
                existing_message = next(
                    item for item in self._messages[replay_task] if item.id == replay_message
                )
                return deepcopy(self._tasks[replay_task]), deepcopy(existing_message)
            current = self._tasks.get(message.task_id)
            if current is None:
                raise NotFoundError(f"task not found: {message.task_id}")
            if current.version != expected_version:
                raise ConcurrentModificationError("task version changed")
            updated = replace(current, version=current.version + 1)
            self._messages[message.task_id].append(deepcopy(message))
            self._tasks[message.task_id] = updated
            self._message_commands[idempotency_key] = (
                message.task_id,
                message.id,
                message.content,
            )
            return deepcopy(updated), deepcopy(message)

    async def cancel(
        self,
        task_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> Task:
        async with self._lock:
            replay_id = self._cancel_commands.get(idempotency_key)
            if replay_id is not None:
                if replay_id != task_id:
                    raise ConcurrentModificationError(
                        "idempotency key was reused for a different task cancellation"
                    )
                return deepcopy(self._tasks[replay_id])
            current = self._tasks.get(task_id)
            if current is None:
                raise NotFoundError(f"task not found: {task_id}")
            if current.version != expected_version:
                raise ConcurrentModificationError("task version changed")
            if current.state.terminal:
                raise ValidationError("terminal task cannot be cancelled")
            updated = replace(
                current,
                state=TaskState.CANCELLED,
                version=current.version + 1,
            )
            self._tasks[task_id] = updated
            self._cancel_commands[idempotency_key] = task_id
            return deepcopy(updated)

    async def messages(self, task_id: str) -> tuple[TaskMessage, ...]:
        async with self._lock:
            if task_id not in self._tasks:
                raise NotFoundError(f"task not found: {task_id}")
            return tuple(deepcopy(self._messages[task_id]))


class InMemoryEventStream:
    def __init__(self) -> None:
        self._events: list[TaskEvent] = []
        self._condition = asyncio.Condition()

    async def publish(
        self, event_type: str, task_id: str, data: dict[str, object]
    ) -> TaskEvent:
        async with self._condition:
            event = TaskEvent(
                sequence=len(self._events) + 1,
                type=event_type,
                task_id=task_id,
                data=deepcopy(data),
                occurred_at=utc_now(),
            )
            self._events.append(event)
            self._condition.notify_all()
            return deepcopy(event)

    async def after(self, sequence: int) -> tuple[TaskEvent, ...]:
        async with self._condition:
            return tuple(deepcopy(event) for event in self._events if event.sequence > sequence)

    async def wait_after(
        self, sequence: int, timeout_seconds: float = 15.0
    ) -> tuple[TaskEvent, ...]:
        async with self._condition:
            existing = tuple(event for event in self._events if event.sequence > sequence)
            if existing:
                return tuple(deepcopy(existing))
            try:
                await asyncio.wait_for(self._condition.wait(), timeout_seconds)
            except TimeoutError:
                return ()
            return tuple(
                deepcopy(event) for event in self._events if event.sequence > sequence
            )
