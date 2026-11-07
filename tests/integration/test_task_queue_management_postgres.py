"""Queue management against a separately provisioned, disposable PostgreSQL server."""

from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg

from personal_assistant.core.jobs import JobState
from personal_assistant.domain import ConcurrentModificationError
from personal_assistant.infrastructure.database import (
    PostgresAdapterConfig,
    build_postgres_adapters,
)

BASE = os.environ.get("PA_TEST_DATABASE_URL")


@unittest.skipUnless(BASE, "requires isolated PA_TEST_DATABASE_URL")
class QueueManagementPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parts = urlsplit(BASE or "")
        if not parts.path.endswith("_test"):
            raise ValueError("requires a dedicated _test database")
        self.admin_url = urlunsplit(parts._replace(path="/postgres"))
        self.database_name = "pa_queue_test_" + uuid4().hex
        admin = await asyncpg.connect(self.admin_url, timeout=5)
        try:
            await admin.execute(f'CREATE DATABASE "{self.database_name}"')
        finally:
            await admin.close()
        self.url = urlunsplit(parts._replace(path="/" + self.database_name))
        self.adapters = build_postgres_adapters(PostgresAdapterConfig(database_url=self.url))
        self.addAsyncCleanup(self.cleanup_database)
        await self.adapters.startup()
        self.queue = self.adapters.job_queue
        self.tick = 0

    async def cleanup_database(self):
        await self.adapters.close()
        admin = await asyncpg.connect(self.admin_url, timeout=5)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=$1 AND pid<>pg_backend_pid()",
                self.database_name,
            )
            await admin.execute(f'DROP DATABASE "{self.database_name}"')
        finally:
            await admin.close()

    async def task(self, name):
        self.tick += 1
        return await self.queue.enqueue(
            kind="agent.start",
            payload={"task_id": name},
            idempotency_key=name,
            available_at=datetime.now(UTC) - timedelta(days=1) + timedelta(seconds=self.tick),
        )

    async def test_order_persists_after_reconnect_and_claim_is_actual_order(self):
        first, second, third = [await self.task(name) for name in ("one", "two", "three")]
        moved = await self.queue.move_ready(job_id=third.id, expected_version=0, direction="top")
        self.assertEqual(third.available_at, moved.available_at)
        self.assertEqual(1, moved.version)
        await self.adapters.close()
        self.adapters = build_postgres_adapters(PostgresAdapterConfig(database_url=self.url))
        await self.adapters.startup()
        self.queue = self.adapters.job_queue
        self.assertEqual(
            [third.id, first.id, second.id], [job.id for job in await self.queue.list_ready_tasks()]
        )
        self.assertEqual(
            [third.id, first.id, second.id],
            [(await self.queue.claim(worker_id="test")).id for _ in range(3)],
        )

    async def test_concurrent_moves_cas_and_cancel_does_not_touch_leased_or_unknown(self):
        first, middle, third = [await self.task(name) for name in ("one", "two", "three")]
        results = await asyncio.gather(
            self.queue.move_ready(job_id=middle.id, expected_version=0, direction="top"),
            self.queue.move_ready(job_id=middle.id, expected_version=0, direction="bottom"),
            return_exceptions=True,
        )
        self.assertEqual(1, sum(isinstance(item, ConcurrentModificationError) for item in results))
        leased = await self.queue.claim(worker_id="test")
        self.assertEqual((), await self.queue.cancel_ready_for_task(leased.payload["task_id"]))
        unknown = await self.queue.mark_unknown(
            job_id=leased.id, worker_id="test", diagnostic_code="UNKNOWN"
        )
        with self.assertRaises(ConcurrentModificationError):
            await self.queue.move_ready(
                job_id=unknown.id, expected_version=unknown.version, direction="top"
            )
        pending = (await self.queue.list_ready_tasks())[0]
        cancelled = await self.queue.cancel_ready_for_task(pending.payload["task_id"])
        self.assertEqual([pending.id], [job.id for job in cancelled])
        self.assertEqual(JobState.FAILED, cancelled[0].state)
        self.assertEqual("TASK_CANCELLED", cancelled[0].error_code)
        replay = await self.queue.enqueue(
            kind=pending.kind, payload=pending.payload, idempotency_key=pending.idempotency_key
        )
        self.assertEqual(JobState.FAILED, replay.state)
        self.assertEqual(JobState.WAITING_RECONCILIATION, (await self.queue.get(unknown.id)).state)

    async def test_future_and_non_task_are_not_reorderable(self):
        future = await self.queue.enqueue(
            kind="agent.start",
            payload={"task_id": "future"},
            idempotency_key="future",
            available_at=datetime.now(UTC) + timedelta(hours=1),
        )
        other = await self.queue.enqueue(kind="event.poll", payload={}, idempotency_key="other")
        for job in (future, other):
            with self.assertRaises(ConcurrentModificationError):
                await self.queue.move_ready(job_id=job.id, expected_version=0, direction="top")
        self.assertEqual((), await self.queue.list_ready_tasks())
        self.assertEqual(other.id, (await self.queue.claim(worker_id="test")).id)
        self.assertIsNone(await self.queue.claim(worker_id="test"))
        self.assertEqual(
            [future.id], [j.id for j in await self.queue.cancel_ready_for_task("future")]
        )

    async def test_queue_change_rolls_back_with_outer_command_and_claim_cancel_race(self):
        first, second = [await self.task(name) for name in ("one", "two")]
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            async with self.adapters.database.transaction():
                await self.queue.move_ready(job_id=second.id, expected_version=0, direction="top")
                await self.queue.cancel_ready_for_task("one")
                raise RuntimeError("rollback")
        self.assertEqual([first.id, second.id], [j.id for j in await self.queue.list_ready_tasks()])
        self.assertEqual(0, (await self.queue.get(first.id)).version)
        claimed, cancelled = await asyncio.gather(
            self.queue.claim(worker_id="test"), self.queue.cancel_ready_for_task("one")
        )
        if claimed.id == first.id:
            self.assertEqual((), cancelled)
            self.assertEqual(JobState.LEASED, (await self.queue.get(first.id)).state)
        else:
            self.assertEqual(second.id, claimed.id)
            self.assertEqual([first.id], [job.id for job in cancelled])

    async def test_unrelated_malformed_metadata_does_not_break_claim(self):
        jobs = [await self.task(str(i)) for i in range(4)]
        async with self.adapters.database.connection() as connection:
            for job, value in zip(jobs, ["bad", "9" * 100, None, True], strict=True):
                await connection.execute(
                    "UPDATE jobs SET metadata=$2 WHERE id=$1",
                    job.id,
                    {"queue_priority": value, "kept": "yes"},
                )
        moved = await self.queue.move_ready(job_id=jobs[-1].id, expected_version=0, direction="top")
        self.assertEqual("yes", moved.metadata["kept"])
        self.assertEqual(jobs[-1].id, (await self.queue.claim(worker_id="test")).id)

    def task_management(self, audit=None):
        from personal_assistant.core.tasks import TaskService
        from personal_assistant.core.tasks.management import TaskManagement
        from personal_assistant.infrastructure.database.records import PostgresRecordStore

        async def unresolved(task_id):
            return False

        tasks = TaskService(
            repository=self.adapters.task_repository,
            queue=self.queue,
            audit=self.adapters.audit_writer,
            events=self.adapters.event_stream,
            unit_of_work=self.adapters.database,
        )
        records = PostgresRecordStore(self.adapters.database, owner_id="owner")
        manager = TaskManagement(
            tasks=tasks,
            jobs=self.queue,
            records=records,
            audit=audit or self.adapters.audit_writer,
            unresolved=unresolved,
        )
        return manager, tasks, records

    async def test_remove_audit_failure_rolls_back_task_queue_visibility_receipt_and_events(self):
        from personal_assistant.domain import TaskState

        class FailAudit:
            async def append(self, event):
                raise RuntimeError("audit rejected")

        manager, tasks, records = self.task_management(FailAudit())
        task = await tasks.create(objective="isolated test", idempotency_key="task-create")
        job = (await self.queue.list_ready_tasks())[0]
        events_before = await self.adapters.event_stream.after(0)
        with self.assertRaisesRegex(RuntimeError, "audit rejected"):
            await manager.remove(task.id, version=task.version, key="remove-once", actor="test")
        self.assertEqual(TaskState.QUEUED, (await tasks.get(task.id)).state)
        self.assertEqual(task.version, (await tasks.get(task.id)).version)
        self.assertEqual(JobState.READY, (await self.queue.get(job.id)).state)
        self.assertIsNone(await records.get("task-visibility", task.id))
        self.assertEqual(events_before, await self.adapters.event_stream.after(0))
        async with self.adapters.database.connection() as connection:
            count = await connection.fetchval(
                "SELECT count(*) FROM runtime_commands WHERE command_key=$1",
                "queue-remove:remove-once",
            )
        self.assertEqual(0, count)
        manager, _, _ = self.task_management()
        result = await manager.remove(
            task.id, version=task.version, key="remove-once", actor="test"
        )
        self.assertEqual("CANCELLED", result["state"])
        self.assertEqual(JobState.FAILED, (await self.queue.get(job.id)).state)
        self.assertTrue((await records.get("task-visibility", task.id))["value"]["hidden"])
        self.assertEqual(
            result,
            await manager.remove(task.id, version=task.version, key="remove-once", actor="test"),
        )

    async def test_move_command_same_key_is_replayed_without_second_move_or_audit(self):
        manager, tasks, records = self.task_management()
        await tasks.create(objective="first", idempotency_key="first")
        await tasks.create(objective="second", idempotency_key="second")
        jobs = await self.queue.list_ready_tasks()
        selected = jobs[-1]
        request = dict(
            job_id=selected.id,
            version=selected.version,
            direction="top",
            key="move-once",
            actor="test",
        )
        first = await manager.move(selected.payload["task_id"], **request)
        self.assertEqual(selected.id, (await self.queue.list_ready_tasks())[0].id)
        await self.adapters.close()
        self.adapters = build_postgres_adapters(PostgresAdapterConfig(database_url=self.url))
        await self.adapters.startup()
        self.queue = self.adapters.job_queue
        manager, _, _ = self.task_management()
        self.assertEqual(first, await manager.move(selected.payload["task_id"], **request))
        self.assertEqual(first["version"], (await self.queue.get(selected.id)).version)
        async with self.adapters.database.connection() as connection:
            count = await connection.fetchval(
                "SELECT count(*) FROM audit_events WHERE event_type='task.queue_moved'"
            )
        self.assertEqual(1, count)
