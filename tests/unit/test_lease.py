from __future__ import annotations

import asyncio
import unittest

from personal_assistant.core.jobs import JobState, LeaseKeepalive
from personal_assistant.infrastructure.memory import InMemoryJobQueue


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


if __name__ == "__main__":
    unittest.main()

