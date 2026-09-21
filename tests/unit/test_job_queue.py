from __future__ import annotations

import unittest
from datetime import datetime

from personal_assistant.core.jobs import JobState, LeaseConflict
from personal_assistant.domain import ValidationError
from personal_assistant.infrastructure.database.job_queue import PostgresJobQueue
from personal_assistant.infrastructure.memory.job_queue import InMemoryJobQueue


class InMemoryJobQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_is_idempotent_per_kind_and_key(self) -> None:
        queue = InMemoryJobQueue()
        first = await queue.enqueue(kind="agent.run", payload={"n": 1}, idempotency_key="same")
        second = await queue.enqueue(kind="agent.run", payload={"n": 1}, idempotency_key="same")
        self.assertEqual(first.id, second.id)
        self.assertEqual({"n": 1}, second.payload)

        with self.assertRaises(ValueError):
            await queue.enqueue(
                kind="agent.run", payload={"n": 2}, idempotency_key="same"
            )

    async def test_only_lease_owner_can_complete(self) -> None:
        queue = InMemoryJobQueue()
        job = await queue.enqueue(kind="agent.run", payload={}, idempotency_key="one")
        claimed = await queue.claim(worker_id="worker-a")
        self.assertEqual(job.id, claimed.id if claimed else None)
        with self.assertRaises(LeaseConflict):
            await queue.complete(job_id=job.id, worker_id="worker-b", result={})

    async def test_unknown_never_returns_to_ready_queue(self) -> None:
        queue = InMemoryJobQueue()
        job = await queue.enqueue(kind="mail.send", payload={}, idempotency_key="send-1")
        await queue.claim(worker_id="worker-a")
        unknown = await queue.mark_unknown(
            job_id=job.id, worker_id="worker-a", diagnostic_code="SMTP_RESULT_UNKNOWN"
        )
        self.assertEqual(JobState.WAITING_RECONCILIATION, unknown.state)
        self.assertIsNone(await queue.claim(worker_id="worker-b"))


class PostgresJobQueueValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_naive_available_at_is_rejected_before_the_transaction(self) -> None:
        queue = PostgresJobQueue(object())  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            await queue.enqueue(
                kind="agent.run",
                payload={},
                idempotency_key="naive-time",
                available_at=datetime(2026, 1, 1, 12, 0, 0),
            )


if __name__ == "__main__":
    unittest.main()
