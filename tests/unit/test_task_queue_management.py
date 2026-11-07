from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from personal_assistant.core.jobs import JobState
from personal_assistant.domain import ConcurrentModificationError, ValidationError
from personal_assistant.infrastructure.memory.job_queue import InMemoryJobQueue


class QueueManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.queue = InMemoryJobQueue()
        self.tick = 0

    def ready_time(self):
        self.tick += 1
        return datetime.now(UTC) - timedelta(days=1) + timedelta(seconds=self.tick)

    async def task(self, name: str, **kwargs):
        kwargs.setdefault("available_at", self.ready_time())
        return await self.queue.enqueue(
            kind="agent.start", payload={"task_id": name}, idempotency_key=name, **kwargs
        )

    async def test_move_changes_actual_claim_order_not_schedule(self):
        first = await self.task("first")
        second = await self.task("second")
        third = await self.task("third")
        moved = await self.queue.move_ready(
            job_id=third.id, expected_version=third.version, direction="top"
        )
        self.assertEqual(third.available_at, moved.available_at)
        self.assertEqual(
            [third.id, first.id, second.id], [j.id for j in await self.queue.list_ready_tasks()]
        )
        self.assertEqual(
            [third.id, first.id, second.id],
            [(await self.queue.claim(worker_id="worker")).id for _ in range(3)],
        )

    async def test_down_and_bottom_keep_other_work_order(self):
        first = await self.task("first")
        other = await self.queue.enqueue(
            kind="event.poll", payload={}, idempotency_key="poll", available_at=self.ready_time()
        )
        second = await self.task("second")
        third = await self.task("third")
        moved = await self.queue.move_ready(job_id=first.id, expected_version=0, direction="down")
        self.assertEqual(
            [second.id, first.id, third.id], [j.id for j in await self.queue.list_ready_tasks()]
        )
        await self.queue.move_ready(
            job_id=first.id, expected_version=moved.version, direction="bottom"
        )
        self.assertEqual(
            [other.id, second.id, third.id, first.id],
            [(await self.queue.claim(worker_id="worker")).id for _ in range(4)],
        )

    async def test_stale_move_conflicts_instead_of_reordering_again(self):
        first = await self.task("first")
        await self.task("second")
        await self.queue.move_ready(job_id=first.id, expected_version=0, direction="bottom")
        with self.assertRaises(ConcurrentModificationError):
            await self.queue.move_ready(job_id=first.id, expected_version=0, direction="top")

    async def test_future_unknown_leased_and_non_task_cannot_be_moved(self):
        future = await self.task("future", available_at=datetime.now(UTC) + timedelta(hours=1))
        leased = await self.task("leased")
        await self.queue.claim(worker_id="worker")
        unknown = await self.task("unknown")
        await self.queue.claim(worker_id="worker")
        unknown = await self.queue.mark_unknown(
            job_id=unknown.id, worker_id="worker", diagnostic_code="RESULT_UNKNOWN"
        )
        other = await self.queue.enqueue(
            kind="event.poll", payload={}, idempotency_key="poll", available_at=self.ready_time()
        )
        for job in (future, leased, unknown, other):
            current = await self.queue.get(job.id)
            with self.assertRaises(ConcurrentModificationError):
                await self.queue.move_ready(
                    job_id=job.id, expected_version=current.version, direction="top"
                )
        self.assertEqual((), await self.queue.list_ready_tasks())
        self.assertEqual(other.id, (await self.queue.claim(worker_id="worker")).id)
        self.assertIsNone(await self.queue.claim(worker_id="worker"))

    async def test_cancel_preserves_original_job_and_does_not_touch_live_or_unknown(self):
        queued = await self.task("same")
        future = await self.queue.enqueue(
            kind="agent.resume",
            payload={"task_id": "same"},
            idempotency_key="future",
            available_at=datetime.now(UTC) + timedelta(hours=1),
        )
        leased = await self.queue.claim(worker_id="worker")
        unknown = await self.queue.enqueue(
            kind="agent.approval", payload={"task_id": "same"}, idempotency_key="unknown"
        )
        await self.queue.claim(worker_id="worker")
        await self.queue.mark_unknown(
            job_id=unknown.id, worker_id="worker", diagnostic_code="UNKNOWN"
        )
        unrelated = await self.task("unrelated")
        cancelled = await self.queue.cancel_ready_for_task("same")
        self.assertEqual([future.id], [job.id for job in cancelled])
        self.assertEqual(JobState.FAILED, cancelled[0].state)
        self.assertEqual("TASK_CANCELLED", cancelled[0].error_code)
        self.assertEqual(JobState.LEASED, (await self.queue.get(leased.id)).state)
        self.assertEqual(JobState.WAITING_RECONCILIATION, (await self.queue.get(unknown.id)).state)
        replay = await self.queue.enqueue(
            kind="agent.resume", payload={"task_id": "same"}, idempotency_key="future"
        )
        self.assertEqual(future.id, replay.id)
        self.assertEqual(JobState.FAILED, replay.state)
        self.assertEqual(unrelated.id, (await self.queue.claim(worker_id="worker")).id)
        self.assertEqual((), await self.queue.cancel_ready_for_task("same"))
        self.assertEqual(queued.id, leased.id)

    async def test_new_work_does_not_jump_reordered_work_and_limits_validate(self):
        first = await self.task("first")
        second = await self.task("second")
        await self.queue.move_ready(job_id=second.id, expected_version=0, direction="up")
        new = await self.task("new")
        self.assertEqual(
            [second.id, first.id, new.id], [j.id for j in await self.queue.list_ready_tasks()]
        )
        for limit in (0, 1001):
            with self.assertRaises(ValidationError):
                await self.queue.list_ready_tasks(limit=limit)
        with self.assertRaises(ValidationError):
            await self.queue.move_ready(job_id=new.id, expected_version=0, direction="invalid")
