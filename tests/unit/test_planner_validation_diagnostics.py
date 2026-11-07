from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from personal_assistant.core.agent.decisions import validation_diagnostic
from personal_assistant.core.agent.planner import StructuredPlanner
from personal_assistant.core.models import ModelOutput
from personal_assistant.domain import (
    NotFoundError,
    RiskLevel,
    TaskState,
    ToolDescriptor,
    ToolOutcome,
    ToolOutcomeKind,
    ValidationError,
)
from personal_assistant.runtime import build_runtime
from tests.unit import test_runtime_delivery as fixtures


class ReplyRouter:
    provider_ids = frozenset({"fixture"})

    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    async def complete(self, request, **kwargs):
        self.requests.append(request)
        return ModelOutput(text=next(self.replies), provider_id="fixture", model_id="fixture")


class PlannerValidationDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.RuntimeDeliveryTests.asyncSetUp

    def runtime(self, replies, *, risk=RiskLevel.READ):
        descriptor = ToolDescriptor(
            id="fixture.index",
            version="1",
            extension_id="fixture",
            risk=risk,
            input_schema={
                "type": "object",
                "properties": {"mode": {"type": "string", "enum": ["full", "incremental"]}},
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
        )
        self.container.tool_registry.publish((descriptor,))
        runtime = build_runtime(self.container)
        router = ReplyRouter(replies)
        runtime.planner = StructuredPlanner(router, "fixture")
        runtime.tools.invoke = AsyncMock(
            return_value=ToolOutcome(ToolOutcomeKind.SUCCESS, result={"indexed": 1})
        )
        return runtime, router

    def call(self, **changes):
        return {
            "kind": "CALL_TOOL",
            "tool_id": "fixture.index",
            "tool_version": "1",
            "arguments": {},
            **changes,
        }

    async def test_invalid_model_replies_are_distinguished_and_never_persist_raw_data(self):
        private = "synthetic-private-argument-do-not-persist"
        replies = [
            (private, "PROPOSAL_INVALID_JSON"),
            ('{"kind":"COMPLETE","kind":"NEEDS_USER"}', "PROPOSAL_INVALID_JSON"),
            (json.dumps(self.call(approval_id=private)), "PROPOSAL_INVALID_SHAPE"),
            (json.dumps(self.call(tool_version=private)), "PROPOSAL_TOOL_NOT_ALLOWED"),
            (json.dumps(self.call(arguments={private: private})), "PROPOSAL_INVALID_ARGUMENTS"),
            (json.dumps({"kind": "COMPLETE", "message": ""}), "PROPOSAL_INVALID_MESSAGE"),
            (json.dumps({"kind": "PLAN", "steps": [private]}), "PROPOSAL_INVALID_DECISION"),
        ]
        for index, (reply, code) in enumerate(replies):
            with self.subTest(code=code):
                runtime, router = self.runtime([reply])
                task = await self.container.tasks.create(
                    objective="index then search fixture", idempotency_key=str(index)
                )
                await runtime.work_once()
                self.assertEqual(
                    TaskState.WAITING_USER, (await self.container.tasks.get(task.id)).state
                )
                runtime.tools.invoke.assert_not_awaited()
                self.assertEqual(1, len(router.requests), "no automatic model retry")
                snapshot = await self.container.checkpoint_store.load("run_" + task.id)
                execution = await self.container.records.get("executions", task.id)
                self.assertEqual(code, snapshot["validation_failure"]["code"])
                self.assertEqual(code, execution["value"]["error_code"])
                self.assertEqual(
                    snapshot["validation_failure"], execution["value"]["validation_failure"]
                )
                self.assertNotIn(private, json.dumps(snapshot, ensure_ascii=False))
                self.assertNotIn(private, json.dumps(execution, ensure_ascii=False))
                self.assertNotIn(private, str(await self.container.tasks.messages(task.id)))

    async def test_successful_explicit_continue_clears_old_failure(self):
        runtime, router = self.runtime(
            [
                json.dumps(self.call(arguments={"bad": True})),
                json.dumps(self.call(arguments={"mode": "incremental"})),
                json.dumps({"kind": "COMPLETE", "message": "fixture indexed"}),
            ]
        )
        task = await self.container.tasks.create(objective="fixture", idempotency_key="resume")
        await runtime.work_once()
        current = await self.container.tasks.get(task.id)
        self.assertIn(
            "validation_failure", await self.container.checkpoint_store.load("run_" + task.id)
        )
        await runtime.request(task.id, version=current.version, mode="agent", key="explicit-resume")
        await runtime.work_once()
        snapshot = await self.container.checkpoint_store.load("run_" + task.id)
        execution = await self.container.records.get("executions", task.id)
        self.assertEqual(TaskState.SUCCEEDED, (await self.container.tasks.get(task.id)).state)
        self.assertNotIn("validation_failure", snapshot)
        self.assertIsNone(execution["value"].get("error_code"))
        self.assertIsNone(execution["value"].get("validation_failure"))
        runtime.tools.invoke.assert_awaited_once()
        self.assertEqual(3, len(router.requests))

    async def test_host_preparation_error_is_not_reported_as_bad_model(self):
        for index, error in enumerate(
            (ValidationError("private preparation"), NotFoundError("private resource"))
        ):
            runtime, _ = self.runtime([json.dumps(self.call())], risk=RiskLevel.EXTERNAL_WRITE)
            runtime.prepare = AsyncMock(side_effect=error)
            task = await self.container.tasks.create(
                objective="fixture", idempotency_key=f"prep-{index}"
            )
            await runtime.work_once()
            snapshot = await self.container.checkpoint_store.load("run_" + task.id)
            expected = "HOST_RESOURCE_UNAVAILABLE" if index else "HOST_PREPARATION_INVALID"
            self.assertEqual(expected, snapshot["validation_failure"]["code"])
            self.assertNotIn("private", json.dumps(snapshot))
            runtime.tools.invoke.assert_not_awaited()

    async def test_gateway_failure_uses_host_diagnostic_and_preserves_unknown(self):
        runtime, _ = self.runtime([json.dumps(self.call())], risk=RiskLevel.EXTERNAL_WRITE)
        runtime.prepare = AsyncMock(
            side_effect=lambda call: replace(call, target={"fixture": "only"})
        )
        runtime.tools.invoke = AsyncMock(side_effect=ValidationError("private execution details"))
        task = await self.container.tasks.create(objective="fixture", idempotency_key="gateway")
        await runtime.work_once()
        snapshot = await self.container.checkpoint_store.load("run_" + task.id)
        self.assertEqual("HOST_TOOL_VALIDATION_FAILED", snapshot["validation_failure"]["code"])
        self.assertEqual(
            TaskState.WAITING_RECONCILIATION, (await self.container.tasks.get(task.id)).state
        )
        self.assertTrue(snapshot["unresolved_external"])
        self.assertNotIn("private execution", json.dumps(snapshot))

    async def test_prompt_examples_are_real_json_and_one_tool_at_a_time(self):
        runtime, router = self.runtime(
            [json.dumps({"kind": "NEEDS_USER", "message": "which fixture?"})]
        )
        await self.container.tasks.create(objective="two steps", idempotency_key="prompt")
        await runtime.work_once()
        instruction = router.requests[0].instruction
        self.assertIn('"kind": "CALL_TOOL"', instruction)
        self.assertIn('"tool_id": "fixture.index"', instruction)
        self.assertIn("exactly one next decision", instruction)
        self.assertIn("wait for its observation", instruction)

    def test_diagnostic_projection_rejects_unknown_codes_and_ignores_stored_messages(self):
        for code in (None, {}, [], 12, "private provider traceback"):
            self.assertIsNone(validation_diagnostic(code))
        first = validation_diagnostic("PROPOSAL_INVALID_ARGUMENTS")
        first["message"] = "private provider traceback"
        self.assertNotIn("private", str(validation_diagnostic(first["code"])))

    async def test_valid_question_after_explicit_continue_clears_failure(self):
        runtime, _ = self.runtime(
            [
                "invalid JSON",
                json.dumps({"kind": "NEEDS_USER", "message": "Which source should I index?"}),
            ]
        )
        task = await self.container.tasks.create(objective="fixture", idempotency_key="question")
        await runtime.work_once()
        current = await self.container.tasks.get(task.id)
        await runtime.request(task.id, version=current.version, mode="agent", key="resume-question")
        await runtime.work_once()
        snapshot = await self.container.checkpoint_store.load("run_" + task.id)
        execution = await self.container.records.get("executions", task.id)
        self.assertEqual(TaskState.WAITING_USER, (await self.container.tasks.get(task.id)).state)
        self.assertNotIn("validation_failure", snapshot)
        self.assertIsNone(execution["value"]["error_code"])
        self.assertIn("conversation_message_ids", snapshot)
        runtime.tools.invoke.assert_not_awaited()

    async def test_invalid_workflow_step_has_host_diagnostic_without_model_call(self):
        runtime, router = self.runtime([])
        task = await self.container.tasks.create(objective="fixture", idempotency_key="workflow")
        await runtime.request(
            task.id,
            version=task.version,
            mode="workflow",
            key="select-workflow",
            workflow={
                "id": "fixture.flow",
                "version": "1",
                "steps": [
                    {
                        "id": "index",
                        "type": "tool",
                        "tool_id": "fixture.index",
                        "tool_version": "1",
                        "arguments": {"unexpected": True},
                    }
                ],
            },
        )
        await runtime.work_once()
        snapshot = await self.container.checkpoint_store.load("run_" + task.id)
        self.assertEqual("HOST_WORKFLOW_INVALID", snapshot["validation_failure"]["code"])
        self.assertEqual([], router.requests)
        runtime.tools.invoke.assert_not_awaited()
