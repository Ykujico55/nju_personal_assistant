from __future__ import annotations

import asyncio
import unittest

from personal_assistant.core.jobs import JobState, LeaseKeepalive, LeaseLost
from personal_assistant.core.jobs.queue import LeaseConflict
from personal_assistant.infrastructure.memory import InMemoryJobQueue


class _FailingHeartbeatQueue:
    """Queue double whose heartbeat always loses the lease."""

    def __init__(self) -> None:
        self.released = 0

    async def heartbeat(self, **kwargs: object) -> None:
        del kwargs
        raise LeaseConflict("lease was reclaimed")

    async def release(self, **kwargs: object) -> None:
        del kwargs
        self.released += 1


class LeaseKeepaliveTests(unittest.IsolatedAsyncioTestCase):
    async def test_heartbeat_runs_independently_and_exit_releases_owned_job(self) -> None:
        queue = InMemoryJobQueue()
        created = await queue.enqueue(kind="long", payload={}, idempotency_key="lease-1")
        await queue.claim(worker_id="worker", lease_seconds=1)
        async with LeaseKeepalive(
            queue,
            job_id=created.id,
            worker_id="worker",
            lease_seconds=1,
            heartbeat_seconds=0.01,
        ) as lease:
            await asyncio.sleep(0.04)
            lease.ensure_owned()
            current = await queue.get(created.id)
            self.assertGreaterEqual(current.version if current else 0, 2)
        released = await queue.get(created.id)
        self.assertEqual(JobState.READY, released.state if released else None)

    async def test_heartbeat_failure_raises_typed_lease_lost_and_releases(self) -> None:
        queue = _FailingHeartbeatQueue()
        with self.assertRaises(LeaseLost):
            async with LeaseKeepalive(
                queue,
                job_id="job-1",
                worker_id="worker",
                lease_seconds=1,
                heartbeat_seconds=0.01,
            ):
                await asyncio.sleep(0.05)
        self.assertEqual(1, queue.released)

    async def test_body_exception_is_not_masked_by_a_lost_lease(self) -> None:
        queue = _FailingHeartbeatQueue()
        with self.assertRaises(ValueError):
            async with LeaseKeepalive(
                queue,
                job_id="job-1",
                worker_id="worker",
                lease_seconds=1,
                heartbeat_seconds=0.01,
            ):
                await asyncio.sleep(0.05)
                raise ValueError("the body failed")
        self.assertEqual(1, queue.released)


if __name__ == "__main__":
    unittest.main()

