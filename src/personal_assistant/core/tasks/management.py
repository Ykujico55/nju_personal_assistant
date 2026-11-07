"""Local queue controls; removing a row never deletes task or action history."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from personal_assistant.core.audit import AuditEvent, AuditWriterPort
from personal_assistant.core.jobs import JobQueuePort
from personal_assistant.core.records import RecordStore
from personal_assistant.core.tasks.service import TaskService
from personal_assistant.domain import ConcurrentModificationError, NotFoundError, Task, TaskState

UnresolvedProbe = Callable[[str], Awaitable[bool]]
VISIBILITY = "task-visibility"


class TaskManagement:
    def __init__(
        self,
        *,
        tasks: TaskService,
        jobs: JobQueuePort,
        records: RecordStore,
        audit: AuditWriterPort,
        unresolved: UnresolvedProbe,
    ) -> None:
        self.tasks, self.jobs, self.records = tasks, jobs, records
        self.audit, self.unresolved = audit, unresolved

    async def list(self, *, include_removed: bool = False) -> list[dict[str, Any]]:
        hidden = {
            row["id"] for row in await self.records.list(VISIBILITY) if row["value"].get("hidden")
        }
        ready = await self.jobs.list_ready_tasks(limit=1000)
        queue: dict[str, Any] = {}
        for position, job in enumerate(ready, 1):
            queue.setdefault(job.payload["task_id"], (position, job))
        # Include older queued tasks even when recent history fills the first page.
        found: dict[str, Task] = {}
        for task_id in queue:
            try:
                task = await self.tasks.get(task_id)
            except NotFoundError:
                continue
            if not task.state.terminal:
                found[task_id] = task
        before = None
        for _ in range(10):
            page = await self.tasks.list_recent(limit=100, before=before)
            for task in page:
                if include_removed or task.id not in hidden or await self._unresolved(task):
                    found.setdefault(task.id, task)
            if len(found) >= 100 or len(page) < 100:
                break
            before = page[-1].id
        ordered = sorted(
            found.values(),
            key=lambda task: (
                queue.get(task.id, (1001,))[0] if not task.state.terminal else 1002,
                -task.created_at.timestamp(),
                task.id,
            ),
        )
        items = []
        for task in ordered[:100]:
            pending = queue.get(task.id) if not task.state.terminal else None
            queued_position, queued_job = pending if pending else (None, None)
            # An interrupted external action remains visible even after a removal race.
            unresolved = await self._unresolved(task)
            removed = task.id in hidden and not unresolved
            if removed and not include_removed:
                continue
            items.append(
                {
                    "id": task.id,
                    "objective": task.objective,
                    "state": task.state.value,
                    "version": task.version,
                    "created_at": task.created_at.isoformat(),
                    "queue_job_id": queued_job.id if queued_job else None,
                    "queue_version": queued_job.version if queued_job else None,
                    "queue_position": queued_position,
                    "removed": removed,
                    "requires_reconciliation": unresolved,
                }
            )
        return items

    async def _unresolved(self, task: Task) -> bool:
        return task.state is TaskState.WAITING_RECONCILIATION or await self.unresolved(task.id)

    async def remove(self, task_id: str, *, version: int, key: str, actor: str) -> dict[str, Any]:
        async def apply() -> dict[str, Any]:
            task = await self.tasks.get(task_id)
            if task.version != version:
                raise ConcurrentModificationError("任务已改变，请刷新后重新选择。")
            if await self._unresolved(task):
                raise ConcurrentModificationError(
                    "外部动作结果未决，必须保留任务供对账，不能移除。"
                )
            if not task.state.terminal:
                task = await self.tasks.cancel(
                    task_id=task_id,
                    expected_version=version,
                    actor=actor,
                    idempotency_key="queue-cancel:" + key,
                )
            old = await self.records.get(VISIBILITY, task_id)
            await self.records.put(
                VISIBILITY, task_id, {"hidden": True}, expected_version=old["version"] if old else 0
            )
            await self.audit.append(
                AuditEvent(
                    "task.removed_from_list", actor, "task", task_id, {"state": task.state.value}
                )
            )
            return {"task_id": task_id, "state": task.state.value, "removed": True}

        return await self.records.command(
            "queue-remove:" + key, {"task_id": task_id, "version": version}, apply
        )

    async def move(
        self,
        task_id: str,
        *,
        job_id: str,
        version: int,
        direction: Literal["up", "down", "top", "bottom"],
        key: str,
        actor: str,
    ) -> dict[str, Any]:
        async def apply() -> dict[str, Any]:
            task = await self.tasks.get(task_id)
            job = await self.jobs.get(job_id)
            if task.state.terminal or job is None or job.payload.get("task_id") != task_id:
                raise ConcurrentModificationError("该任务不在可调整的待执行队列，请刷新。")
            if await self._unresolved(task):
                raise ConcurrentModificationError("外部动作结果未决，不能通过排序重新派发。")
            moved = await self.jobs.move_ready(
                job_id=job_id, expected_version=version, direction=direction
            )
            await self.audit.append(
                AuditEvent(
                    "task.queue_moved",
                    actor,
                    "task",
                    task_id,
                    {"job_id": job_id, "direction": direction},
                )
            )
            return {"task_id": task_id, "job_id": moved.id, "version": moved.version}

        return await self.records.command(
            "queue-move:" + key,
            {"task_id": task_id, "job_id": job_id, "version": version, "direction": direction},
            apply,
        )
