from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from personal_assistant.bootstrap import build_container
from personal_assistant.domain import (
    ConcurrentModificationError,
    RiskLevel,
    TaskRun,
    TaskState,
    ToolDescriptor,
    ValidationError,
)
from personal_assistant.settings import Settings


class DecisionDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id="fixture.read",
            version="1",
            extension_id="fixture",
            extension_version="1",
            risk=RiskLevel.READ,
            input_schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
        )

    async def test_model_cannot_grant_permissions_or_select_unknown_version(self) -> None:
        from personal_assistant.core.agent.decisions import DecisionValidator

        validator = DecisionValidator((self.descriptor(),), grants=frozenset())
        run = TaskRun(id="r", task_id="t", objective="fixture")
        for proposal in (
            {"kind": "CALL_TOOL", "tool_id": "fixture.read", "tool_version": "2", "arguments": {}},
            {
                "kind": "CALL_TOOL",
                "tool_id": "fixture.read",
                "tool_version": "1",
                "arguments": {},
                "granted_capabilities": ["mail.send"],
            },
            {
                "kind": "CALL_TOOL",
                "tool_id": "fixture.read",
                "tool_version": "1",
                "arguments": {"count": True},
            },
        ):
            with self.subTest(proposal=proposal), self.assertRaises(ValidationError):
                await validator.validate(proposal, run)

    async def test_json_duplicate_keys_and_prohibited_tool_are_rejected(self) -> None:
        from personal_assistant.core.agent.decisions import DecisionValidator, parse_proposal

        with self.assertRaises(ValidationError):
            parse_proposal('{"kind":"COMPLETE","kind":"CALL_TOOL"}')
        validator = DecisionValidator(
            (replace(self.descriptor(), risk=RiskLevel.PROHIBITED),), grants=frozenset()
        )
        with self.assertRaises(ValidationError):
            await validator.validate(
                {
                    "kind": "CALL_TOOL",
                    "tool_id": "fixture.read",
                    "tool_version": "1",
                    "arguments": {},
                },
                TaskRun(id="r", task_id="t", objective="fixture"),
            )

    async def test_valid_call_binds_host_task_and_capabilities(self) -> None:
        from personal_assistant.core.agent.decisions import DecisionValidator

        validator = DecisionValidator((self.descriptor(),), grants=frozenset({"host.read"}))
        decision = await validator.validate(
            {
                "kind": "CALL_TOOL",
                "tool_id": "fixture.read",
                "tool_version": "1",
                "arguments": {"count": 1},
            },
            TaskRun(id="r", task_id="t", objective="fixture"),
        )
        self.assertEqual("t", decision.tool_call.task_id)
        self.assertEqual(frozenset({"fixture.read"}), decision.tool_call.workflow_allowed_tools)
        self.assertEqual(frozenset({"host.read"}), decision.tool_call.granted_capabilities)


class RuntimeDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()
        self.container = build_container(settings)
        self.addAsyncCleanup(self.container.aclose)

    async def test_no_model_becomes_waiting_and_duplicate_job_does_not_duplicate_message(
        self,
    ) -> None:
        from personal_assistant.runtime import build_runtime

        task = await self.container.tasks.create(objective="fixture", idempotency_key="create")
        runtime = build_runtime(self.container)
        self.assertTrue(await runtime.work_once())
        current = await self.container.tasks.get(task.id)
        self.assertEqual(TaskState.WAITING_USER, current.state)
        messages = await self.container.tasks.messages(task.id)
        self.assertTrue(any("模型" in message.content for message in messages))
        await self.container.jobs.enqueue(
            kind="agent.start", payload={"task_id": task.id}, idempotency_key="duplicate"
        )
        self.assertTrue(await runtime.work_once())
        self.assertEqual(messages, await self.container.tasks.messages(task.id))

    async def test_stale_resume_preserves_task_and_same_key_replays_original_receipt(self) -> None:
        from personal_assistant.runtime import build_runtime

        runtime = build_runtime(self.container)
        task = await self.container.tasks.create(objective="fixture", idempotency_key="c")
        await runtime.work_once()
        task = await self.container.tasks.get(task.id)
        with self.assertRaises(ConcurrentModificationError):
            await runtime.request(task.id, version=task.version - 1, mode="agent", key="stale")
        first = await runtime.request(task.id, version=task.version, mode="agent", key="resume")
        await runtime.work_once()
        self.assertEqual(
            first, await runtime.request(task.id, version=task.version, mode="agent", key="resume")
        )
        with self.assertRaises(ConcurrentModificationError):
            await runtime.request(
                task.id,
                version=task.version,
                mode="workflow",
                key="resume",
                workflow={"id": "other", "version": "1", "steps": []},
            )

    async def test_interrupted_dispatch_never_calls_gateway_again(self) -> None:
        from personal_assistant.runtime import build_runtime

        runtime = build_runtime(self.container)
        task = await self.container.tasks.create(objective="fixture", idempotency_key="c")
        run = TaskRun(id="run_" + task.id, task_id=task.id, objective=task.objective)
        await self.container.run_repository.add(run)
        await self.container.checkpoint_store.save(run, {"phase": "dispatching", "risk": "R2"})
        with patch.object(self.container.tool_gateway, "invoke", new=AsyncMock()) as invoke:
            await runtime.work_once()
        invoke.assert_not_called()
        self.assertEqual(
            TaskState.WAITING_RECONCILIATION, (await self.container.tasks.get(task.id)).state
        )

    async def test_cancelled_task_never_starts_a_run(self) -> None:
        from personal_assistant.runtime import build_runtime

        task = await self.container.tasks.create(objective="fixture", idempotency_key="c")
        await self.container.tasks.cancel(
            task_id=task.id, expected_version=task.version, actor="owner", idempotency_key="cancel"
        )
        runtime = build_runtime(self.container)
        await runtime.work_once()
        self.assertEqual(TaskState.CANCELLED, (await self.container.tasks.get(task.id)).state)


class RecordDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_fingerprint_distinguishes_integer_boolean_and_cas_is_strict(
        self,
    ) -> None:
        from personal_assistant.infrastructure.memory.records import InMemoryRecordStore

        store = InMemoryRecordStore()
        saved = await store.put("rules", "r", {"value": 1}, expected_version=0)
        with self.assertRaises(ConcurrentModificationError):
            await store.put("rules", "r", {"value": 2}, expected_version=0)
        first = await store.command("k", {"value": 1}, AsyncMock(return_value={"ok": 1}))
        with self.assertRaises(ConcurrentModificationError):
            await store.command("k", {"value": True}, AsyncMock(return_value={"ok": 2}))
        self.assertEqual(
            first, await store.command("k", {"value": 1}, AsyncMock(side_effect=AssertionError()))
        )
        self.assertEqual(1, saved["version"])


class RuntimeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = RuntimeDeliveryTests.asyncSetUp

    async def test_model_failure_pauses_task_instead_of_leaving_running(self):
        from personal_assistant.runtime import build_runtime

        task = await self.container.tasks.create(objective="fixture", idempotency_key="failure")
        runtime = build_runtime(self.container)
        runtime.planner.propose = AsyncMock(side_effect=RuntimeError("synthetic provider outage"))
        await runtime.work_once()
        self.assertEqual(TaskState.WAITING_USER, (await self.container.tasks.get(task.id)).state)
        self.assertEqual(
            TaskState.WAITING_USER,
            (await self.container.run_repository.get("run_" + task.id)).state,
        )

    async def test_tool_grants_are_not_inherited_from_another_extension(self):
        from personal_assistant.core.agent.decisions import DecisionValidator

        descriptor = DecisionDeliveryTests().descriptor()
        validator = DecisionValidator(
            (descriptor,),
            grants={
                "fixture.read": frozenset({"read.fixture"}),
                "other.send": frozenset({"mail.send"}),
            },
        )
        result = await validator.validate(
            {"kind": "CALL_TOOL", "tool_id": "fixture.read", "tool_version": "1", "arguments": {}},
            TaskRun(id="r", task_id="t", objective="fixture"),
        )
        self.assertEqual(frozenset({"read.fixture"}), result.tool_call.granted_capabilities)

    async def test_active_rule_snapshot_does_not_drift_on_resume(self):
        from personal_assistant.core.governance import RuleService
        from personal_assistant.runtime import build_runtime

        rules = RuleService(self.container.records)
        rule = await rules.propose(
            scope={"category": "general"}, preferences={"language": "zh"}, key="propose"
        )
        await rules.activate(
            rule["id"],
            version=1,
            tests=[
                {"metadata": {"category": "general"}, "matches": True},
                {"metadata": {}, "matches": False},
            ],
            key="activate",
        )
        task = await self.container.tasks.create(objective="fixture", idempotency_key="rules")
        runtime = build_runtime(self.container)
        await runtime.work_once()
        snapshot = await self.container.checkpoint_store.load("run_" + task.id)
        self.assertEqual("zh", snapshot["rules"][0]["value"]["preferences"]["language"])
        await rules.disable(rule["id"], version=2, key="disable")
        current = await self.container.tasks.get(task.id)
        await runtime.request(task.id, version=current.version, mode="agent", key="resume")
        await runtime.work_once()
        self.assertEqual(
            snapshot["rules"],
            (await self.container.checkpoint_store.load("run_" + task.id))["rules"],
        )


class PlannerConsentResumeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = RuntimeDeliveryTests.asyncSetUp

    async def test_disclosure_resume_does_not_change_fields_with_its_own_status_message(self):
        from personal_assistant.api.v1.delivery import task_model_request
        from personal_assistant.core.agent.planner import StructuredPlanner
        from personal_assistant.core.models import ModelOutput, ModelRouter, RecipientIdentity
        from personal_assistant.core.models.disclosure import DisclosureConsentService
        from personal_assistant.infrastructure.memory import InMemoryDisclosureConsentStore
        from personal_assistant.runtime import build_runtime

        class Provider:
            provider_id = "fixture.remote"
            is_remote = True
            recipient = RecipientIdentity(
                provider_id="fixture.remote",
                adapter="fake",
                endpoint="https://fixture.example",
                model_id="fake",
            )
            calls = 0

            async def complete(self, request):
                self.calls += 1
                return ModelOutput(
                    text='{"kind":"COMPLETE","message":"fixture done"}',
                    provider_id=self.provider_id,
                    model_id="fake",
                )

        provider = Provider()
        disclosures = DisclosureConsentService(
            InMemoryDisclosureConsentStore(), recipients={provider.provider_id: provider.recipient}
        )
        self.container.disclosures = disclosures
        router = ModelRouter((provider,), disclosure=disclosures)
        runtime = build_runtime(self.container)
        runtime.planner = StructuredPlanner(router, provider.provider_id)
        task = await self.container.tasks.create(objective="synthetic fixture", idempotency_key="c")
        await runtime.work_once()
        self.assertEqual(0, provider.calls)
        selected, request = await task_model_request(self.container, task.id)
        preview = disclosures.preview(request, provider_id=selected)
        consent = await disclosures.confirm(
            request,
            provider_id=selected,
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="allow",
        )
        current = await self.container.tasks.get(task.id)
        await runtime.request(
            task.id, version=current.version, mode="agent", key="resume", consent_id=consent.id
        )
        await runtime.work_once()
        self.assertEqual(1, provider.calls)
        self.assertEqual(TaskState.SUCCEEDED, (await self.container.tasks.get(task.id)).state)


class RuntimeCancellationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = RuntimeDeliveryTests.asyncSetUp

    async def test_task_cancel_interrupts_inflight_r2_and_records_unknown(self):
        import asyncio

        from personal_assistant.runtime import build_runtime

        descriptor = replace(DecisionDeliveryTests().descriptor(), risk=RiskLevel.EXTERNAL_WRITE)
        self.container.tool_registry.publish((descriptor,))
        task = await self.container.tasks.create(objective="fixture", idempotency_key="c")
        runtime = build_runtime(self.container)
        runtime.prepare = AsyncMock(side_effect=lambda call: call)
        runtime.planner.propose = AsyncMock(
            return_value={
                "kind": "CALL_TOOL",
                "tool_id": descriptor.id,
                "tool_version": descriptor.version,
                "arguments": {},
            }
        )
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def invoke(call):
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()

        with patch.object(
            self.container.tool_gateway, "invoke", new=AsyncMock(side_effect=invoke)
        ) as dispatch:
            running = asyncio.create_task(runtime.work_once())
            try:
                await asyncio.wait_for(started.wait(), 2)
                current = await self.container.tasks.get(task.id)
                await self.container.tasks.cancel(
                    task_id=task.id,
                    expected_version=current.version,
                    actor="owner",
                    idempotency_key="cancel",
                )
                await asyncio.wait_for(cancelled.wait(), 1)
                await asyncio.wait_for(running, 2)
                self.assertEqual(1, dispatch.await_count)
            finally:
                if not running.done():
                    running.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await running
        self.assertEqual(TaskState.CANCELLED, (await self.container.tasks.get(task.id)).state)
        execution = await self.container.records.get("executions", task.id)
        self.assertEqual("WAITING_RECONCILIATION", execution["value"]["state"])


class InitialWorkflowJobTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = RuntimeDeliveryTests.asyncSetUp

    async def test_configuring_initial_workflow_does_not_enqueue_duplicate_question(self):
        from personal_assistant.runtime import build_runtime

        task = await self.container.tasks.create(objective="fixture", idempotency_key="c")
        runtime = build_runtime(self.container)
        flow = {
            "id": "fixture.flow",
            "version": "1",
            "steps": [
                {
                    "id": "ask",
                    "type": "ask",
                    "field": "answer",
                    "question": "one question",
                    "schema": {"type": "string"},
                },
                {"id": "done", "type": "archive"},
            ],
        }
        await runtime.request(
            task.id, version=task.version, mode="workflow", workflow=flow, key="select"
        )
        while await runtime.work_once():
            pass
        self.assertEqual(1, len(await self.container.tasks.messages(task.id)))
