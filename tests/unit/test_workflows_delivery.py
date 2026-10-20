from __future__ import annotations

import unittest

from personal_assistant.domain import ConcurrentModificationError, ValidationError
from personal_assistant.infrastructure.memory.records import InMemoryRecordStore


class WorkflowDeliveryTests(unittest.TestCase):
    def workflow(self) -> dict:
        return {
            "id": "fixture.flow",
            "version": "1",
            "steps": [
                {
                    "id": "ask",
                    "type": "ask",
                    "field": "answer",
                    "question": "Material?",
                    "schema": {"type": "string", "minLength": 1},
                },
                {
                    "id": "read",
                    "type": "tool",
                    "tool_id": "fixture.read",
                    "tool_version": "1",
                    "arguments": {"text": {"$value": "inputs.answer"}},
                },
                {"id": "archive", "type": "archive"},
            ],
        }

    def test_gap_input_then_tool_and_archive(self) -> None:
        from personal_assistant.core.workflows import next_proposal

        definition, snapshot = self.workflow(), {}
        self.assertEqual("NEEDS_USER", next_proposal(definition, snapshot, {})["kind"])
        step = next_proposal(definition, snapshot, {"answer": "fixture"})
        self.assertEqual("CALL_TOOL", step["kind"])
        self.assertEqual({"text": "fixture"}, step["arguments"])
        snapshot["cursor"] = 2
        self.assertEqual(
            "COMPLETE", next_proposal(definition, snapshot, {"answer": "fixture"})["kind"]
        )

    def test_scripts_unknown_steps_and_unresolved_references_fail_explicitly(self) -> None:
        from personal_assistant.core.workflows import next_proposal, validate_workflow

        for changes in (
            {"type": "javascript", "script": "alert(1)"},
            {
                "type": "tool",
                "tool_id": "fixture",
                "tool_version": "1",
                "arguments": {},
                "grants": ["mail.send"],
            },
        ):
            definition = self.workflow()
            definition["steps"][0] = {"id": "bad", **changes}
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                validate_workflow(definition)
        snapshot = {"cursor": 1}
        with self.assertRaises(ValidationError):
            next_proposal(self.workflow(), snapshot, {})

    def test_workflow_snapshot_cannot_change_during_resumption(self) -> None:
        from personal_assistant.core.workflows import next_proposal

        snapshot = {}
        next_proposal(self.workflow(), snapshot, {})
        changed = self.workflow()
        changed["version"] = "2"
        with self.assertRaises(ConcurrentModificationError):
            next_proposal(changed, snapshot, {"answer": "fixture"})


class RuleDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_rule_needs_positive_and_negative_evidence_and_explicit_activation(self) -> None:
        from personal_assistant.core.governance import RuleService

        service = RuleService(InMemoryRecordStore())
        draft = await service.propose(
            scope={"workflow_id": "fixture.flow"}, preferences={"language": "zh"}, key="draft"
        )
        self.assertEqual((), await service.active())
        with self.assertRaises(ValidationError):
            await service.activate(draft["id"], version=1, tests=[], key="bad")
        tests = [
            {"metadata": {"workflow_id": "fixture.flow"}, "matches": True},
            {"metadata": {"workflow_id": "other.flow"}, "matches": False},
        ]
        active = await service.activate(draft["id"], version=1, tests=tests, key="activate")
        self.assertEqual("ACTIVE", active["value"]["state"])
        await service.disable(draft["id"], version=active["version"], key="disable")
        self.assertEqual((), await service.active())

    async def test_rule_cannot_change_policy_grants_or_store_arbitrary_code(self) -> None:
        from personal_assistant.core.governance import RuleService

        service = RuleService(InMemoryRecordStore())
        for preferences in (
            {"external_writes_enabled": True},
            {"javascript": "x"},
            {"password": "synthetic"},
            {"language": "zh", "auto_approve": True},
        ):
            with self.subTest(preferences=preferences), self.assertRaises(ValidationError):
                await service.propose(
                    scope={"workflow_id": "fixture.flow"}, preferences=preferences, key="bad"
                )


class SchedulerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_coalesces_offline_poll_and_persists_last_fire(self) -> None:
        from datetime import UTC, datetime, timedelta

        from personal_assistant.core.scheduling import DurableScheduler
        from personal_assistant.infrastructure.memory.job_queue import InMemoryJobQueue

        records, queue = InMemoryRecordStore(), InMemoryJobQueue()
        scheduler = DurableScheduler(records, queue)
        now = datetime(2026, 10, 13, tzinfo=UTC)
        definition = {
            "id": "poll",
            "source_id": "source",
            "extension_id": "fixture",
            "extension_version": "1",
            "interval_seconds": 300,
            "timezone": "Asia/Shanghai",
            "misfire_policy": "coalesce",
        }
        await scheduler.configure(definition, key="config", now=now)
        fired = await scheduler.tick(now=now + timedelta(hours=3))
        self.assertEqual(1, fired)
        self.assertEqual(
            0, await DurableScheduler(records, queue).tick(now=now + timedelta(hours=3))
        )
        job = await queue.claim(worker_id="fixture")
        self.assertEqual("event.poll", job.kind)

    async def test_scheduler_rejects_external_tools_and_unknown_misfire_policy(self) -> None:
        from personal_assistant.core.scheduling import DurableScheduler
        from personal_assistant.infrastructure.memory.job_queue import InMemoryJobQueue

        scheduler = DurableScheduler(InMemoryRecordStore(), InMemoryJobQueue())
        for definition in (
            {"id": "bad", "tool_id": "mail.send", "interval_seconds": 300},
            {
                "id": "bad",
                "source_id": "x",
                "extension_id": "fixture",
                "extension_version": "1",
                "interval_seconds": 300,
                "timezone": "Asia/Shanghai",
                "misfire_policy": "replay_everything",
            },
        ):
            with self.subTest(definition=definition), self.assertRaises(ValidationError):
                await scheduler.configure(definition, key="bad")
