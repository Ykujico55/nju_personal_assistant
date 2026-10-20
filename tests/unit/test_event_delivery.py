from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from personal_assistant.domain import ValidationError
from personal_assistant.infrastructure.memory.records import InMemoryRecordStore


class EventDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_poll_dedupes_across_jobs_and_replay_does_not_poll_again(self) -> None:
        from personal_assistant.core.event_delivery import EventCollector

        records = InMemoryRecordStore()
        result = {
            "events": [
                {
                    "type": "fixture.arrived",
                    "source_dedupe_key": "one",
                    "occurred_at": "2026-10-13T10:00:00+00:00",
                    "payload": {"text": "fixture"},
                }
            ],
            "source_dedupe_keys": ["one"],
            "next_cursor": "c1",
        }
        poll = AsyncMock(return_value=result)
        collector = EventCollector(records, poll)
        request = {
            "extension_id": "fixture",
            "extension_version": "1",
            "source_id": "fixture.source",
        }
        first = await collector.collect(request, job_id="job1")
        self.assertEqual(first, await collector.collect(request, job_id="job1"))
        self.assertEqual(1, poll.await_count)
        await collector.collect(request, job_id="job2")
        self.assertEqual(1, len(await records.list("domain-events")))
        self.assertEqual(
            "c1", (await records.get("event-cursors", "fixture:fixture.source"))["value"]["cursor"]
        )

    async def test_bad_batch_cannot_advance_cursor_or_publish_partial_events(self) -> None:
        from personal_assistant.core.event_delivery import EventCollector

        records = InMemoryRecordStore()
        poll = AsyncMock(
            return_value={
                "events": [
                    {
                        "type": "x",
                        "source_dedupe_key": "one",
                        "occurred_at": "bad-time",
                        "payload": {},
                    }
                ],
                "source_dedupe_keys": ["other"],
                "next_cursor": "c1",
            }
        )
        collector = EventCollector(records, poll)
        with self.assertRaises(ValidationError):
            await collector.collect(
                {
                    "extension_id": "fixture",
                    "extension_version": "1",
                    "source_id": "fixture.source",
                },
                job_id="job1",
            )
        self.assertEqual((), await records.list("domain-events"))
        self.assertIsNone(await records.get("event-cursors", "fixture:fixture.source"))
