from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from personal_assistant.bootstrap import build_container
from personal_assistant.core import scheduling
from personal_assistant.core.scheduling import DurableScheduler, cron_next
from personal_assistant.domain import TaskState, ValidationError
from personal_assistant.infrastructure.memory.job_queue import InMemoryJobQueue
from personal_assistant.infrastructure.memory.records import InMemoryRecordStore
from personal_assistant.settings import Settings
from personal_assistant.workers.agent_worker import loop


class CronBacklogTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_backlog_obeys_each_policy_and_does_not_requeue(self) -> None:
        current = datetime(2026, 10, 30, 12, 5, 30, tzinfo=UTC)
        latest = current.replace(second=0)
        for policy, expected in (
            ("coalesce", [latest]),
            ("skip", []),
            ("catch_up", [latest - timedelta(minutes=i) for i in (2, 1, 0)]),
        ):
            with self.subTest(policy=policy):
                records, queue = InMemoryRecordStore(), InMemoryJobQueue()
                scheduler = DurableScheduler(records, queue)
                await scheduler.configure(
                    {
                        "id": "poll", "source_id": "source", "extension_id": "fixture",
                        "extension_version": "1", "cron": "* * * * *", "timezone": "UTC",
                        "misfire_policy": policy, "max_catch_up": 3,
                    },
                    key="configure", now=current - timedelta(days=730),
                )
                with patch.object(
                    scheduling, "_cron_matches", wraps=scheduling._cron_matches
                ) as matches:
                    self.assertEqual(len(expected), await scheduler.tick(now=current))
                # A minutely schedule needs only the nearest few matches, not two
                # years of history; use a deterministic work budget, not a timer.
                self.assertLessEqual(matches.call_count, 20)
                self.assertEqual(0, await DurableScheduler(records, queue).tick(now=current))
                saved = await records.get("schedules", "poll")
                assert saved is not None
                self.assertEqual(
                    (latest + timedelta(minutes=1)).isoformat(), saved["value"]["next_at"]
                )
                keys = []
                for _ in expected:
                    job = await queue.claim(worker_id="probe")
                    assert job is not None
                    self.assertEqual("event.poll", job.kind)
                    keys.append(job.idempotency_key)
                    await queue.complete(job_id=job.id, worker_id="probe", result={})
                self.assertCountEqual(
                    [f"schedule:poll:{time.isoformat()}" for time in expected], keys
                )
                self.assertIsNone(await queue.claim(worker_id="probe"))

    async def test_exact_due_and_skip_grace_remain_inclusive(self) -> None:
        start = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
        due = start + timedelta(minutes=1)
        for offset, count in ((-1, 0), (0, 1), (60, 1), (61, 0)):
            with self.subTest(offset=offset):
                records, queue = InMemoryRecordStore(), InMemoryJobQueue()
                scheduler = DurableScheduler(records, queue)
                await scheduler.configure(
                    {
                        "id": "poll", "source_id": "source", "extension_id": "fixture",
                        "extension_version": "1", "cron": "* * * * *", "timezone": "UTC",
                        "misfire_policy": "skip",
                    }, key="configure", now=start,
                )
                current = due + timedelta(seconds=offset)
                self.assertEqual(count, await scheduler.tick(now=current))
                job = await queue.claim(worker_id="probe")
                if count:
                    assert job is not None
                    self.assertEqual(
                        f"schedule:poll:{current.replace(second=0).isoformat()}",
                        job.idempotency_key,
                    )
                else:
                    self.assertIsNone(job)

    async def test_catch_up_does_not_include_occurrences_before_first_due(self) -> None:
        records, queue = InMemoryRecordStore(), InMemoryJobQueue()
        scheduler = DurableScheduler(records, queue)
        start = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
        await scheduler.configure(
            {
                "id": "poll", "source_id": "source", "extension_id": "fixture",
                "extension_version": "1", "cron": "* * * * *", "timezone": "UTC",
                "misfire_policy": "catch_up", "max_catch_up": 10,
            }, key="configure", now=start,
        )
        self.assertEqual(2, await scheduler.tick(now=start + timedelta(minutes=2)))
        keys = []
        for _ in range(2):
            job = await queue.claim(worker_id="probe")
            assert job is not None
            keys.append(job.idempotency_key)
            await queue.complete(job_id=job.id, worker_id="probe", result={})
        self.assertCountEqual(
            [f"schedule:poll:{(start + timedelta(minutes=i)).isoformat()}" for i in (1, 2)], keys
        )
        self.assertIsNone(await queue.claim(worker_id="probe"))

    async def test_timezone_dst_and_day_rules_match_forward_cron(self) -> None:
        fixtures = (
            # The two repeated local 01:30 occurrences are distinct UTC events.
            ("30 1 * * *", "America/New_York", datetime(2026, 11, 1, 4, tzinfo=UTC),
             datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
             [datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
              datetime(2026, 11, 1, 6, 30, tzinfo=UTC)]),
            # Local 02:30 does not exist on the spring transition date.
            ("30 2 * * *", "America/New_York", datetime(2026, 3, 7, 0, tzinfo=UTC),
             datetime(2026, 3, 9, 6, 30, tzinfo=UTC),
             [datetime(2026, 3, 7, 7, 30, tzinfo=UTC),
              datetime(2026, 3, 9, 6, 30, tzinfo=UTC)]),
            # Restricted month-day and weekday retain OR semantics.
            ("0 9 15 * 1", "Asia/Shanghai", datetime(2026, 10, 13, 0, tzinfo=UTC),
             datetime(2026, 10, 19, 1, tzinfo=UTC),
             [datetime(2026, 10, 15, 1, tzinfo=UTC),
              datetime(2026, 10, 19, 1, tzinfo=UTC)]),
        )
        for expression, zone, start, current, expected in fixtures:
            with self.subTest(expression=expression, current=current):
                records, queue = InMemoryRecordStore(), InMemoryJobQueue()
                scheduler = DurableScheduler(records, queue)
                await scheduler.configure(
                    {
                        "id": "poll", "source_id": "source", "extension_id": "fixture",
                        "extension_version": "1", "cron": expression, "timezone": zone,
                        "misfire_policy": "catch_up", "max_catch_up": 10,
                    }, key="configure", now=start,
                )
                self.assertEqual(len(expected), await scheduler.tick(now=current))
                keys = []
                for _ in expected:
                    job = await queue.claim(worker_id="probe")
                    assert job is not None
                    keys.append(job.idempotency_key)
                    await queue.complete(job_id=job.id, worker_id="probe", result={})
                self.assertCountEqual(
                    [f"schedule:poll:{time.isoformat()}" for time in expected], keys
                )
                saved = await records.get("schedules", "poll")
                assert saved is not None
                self.assertEqual(
                    cron_next(expression, current, zone).isoformat(), saved["value"]["next_at"]
                )
                self.assertIsNone(await queue.claim(worker_id="probe"))

    async def test_sparse_yearly_backlog_keeps_only_latest_bounded_occurrences(self) -> None:
        records, queue = InMemoryRecordStore(), InMemoryJobQueue()
        scheduler = DurableScheduler(records, queue)
        await scheduler.configure(
            {
                "id": "annual", "source_id": "source", "extension_id": "fixture",
                "extension_version": "1", "cron": "0 0 1 1 *", "timezone": "UTC",
                "misfire_policy": "catch_up", "max_catch_up": 2,
            }, key="configure", now=datetime(2020, 1, 2, tzinfo=UTC),
        )
        current = datetime(2026, 10, 30, tzinfo=UTC)
        self.assertEqual(2, await scheduler.tick(now=current))
        keys = []
        for _ in range(2):
            job = await queue.claim(worker_id="probe")
            assert job is not None
            keys.append(job.idempotency_key)
            await queue.complete(job_id=job.id, worker_id="probe", result={})
        self.assertCountEqual(
            [f"schedule:annual:{datetime(year, 1, 1, tzinfo=UTC).isoformat()}"
             for year in (2025, 2026)], keys,
        )
        self.assertIsNone(await queue.claim(worker_id="probe"))
        saved = await records.get("schedules", "annual")
        assert saved is not None
        self.assertEqual(
            datetime(2027, 1, 1, tzinfo=UTC).isoformat(), saved["value"]["next_at"]
        )

    async def test_invalid_cron_still_fails_explicitly_without_a_record(self) -> None:
        records, queue = InMemoryRecordStore(), InMemoryJobQueue()
        scheduler = DurableScheduler(records, queue)
        with self.assertRaises(ValidationError):
            await scheduler.configure(
                {
                    "id": "poll", "source_id": "source", "extension_id": "fixture",
                    "extension_version": "1", "cron": "60 * * * *", "timezone": "UTC",
                    "misfire_policy": "coalesce",
                }, key="configure", now=datetime(2026, 10, 30, tzinfo=UTC),
            )
        self.assertIsNone(await records.get("schedules", "poll"))
        self.assertIsNone(await queue.claim(worker_id="probe"))

    async def test_overdue_cron_does_not_block_unrelated_worker_task(self) -> None:
        root = Path(self.enterContext(TemporaryDirectory()))
        with patch.dict("os.environ", {}, clear=True):
            settings = replace(
                Settings.from_env(), extension_root=root / "extensions",
                artifact_root=root / "artifacts",
            )
        container = build_container(settings)
        self.addAsyncCleanup(container.aclose)
        task = await container.tasks.create(objective="offline fixture", idempotency_key="task")
        await DurableScheduler(container.records, container.jobs).configure(
            {
                "id": "poll", "source_id": "source", "extension_id": "fixture",
                "extension_version": "1", "cron": "* * * * *", "timezone": "UTC",
                "misfire_policy": "coalesce",
            }, key="configure", now=datetime.now(UTC) - timedelta(days=2),
        )
        await loop(container, asyncio.Event(), once=True)
        self.assertEqual(TaskState.WAITING_USER, (await container.tasks.get(task.id)).state)
        messages = await container.tasks.messages(task.id)
        self.assertTrue(any("模型尚未配置" in message.content for message in messages))
