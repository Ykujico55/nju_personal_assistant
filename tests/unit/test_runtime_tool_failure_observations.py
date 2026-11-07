"""Failed tool attempts remain distinguishable from successful empty results."""

from __future__ import annotations

import json
import unittest

from personal_assistant.core.agent.planner import StructuredPlanner
from personal_assistant.core.models import ModelRouter
from personal_assistant.core.tools import OutcomeUnknownError, ToolGateway, ToolPolicy
from personal_assistant.core.tools.gateway import DefinitiveToolFailure
from personal_assistant.domain import RiskLevel, TaskState, ToolDescriptor
from personal_assistant.runtime import build_runtime
from tests.unit import test_runtime_delivery as fixtures
from tests.unit.test_runtime_conversation_context import ConversationProvider, messages_field


class SequenceExecutor:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    async def execute(self, descriptor, arguments, context):
        self.calls.append(dict(arguments))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def call(query="fixture"):
    return json.dumps(
        {
            "kind": "CALL_TOOL",
            "tool_id": "fixture.lookup",
            "tool_version": "1",
            "arguments": {"query": query},
        }
    )


def ask(message="Please check the configured source."):
    return json.dumps({"kind": "NEEDS_USER", "message": message})


def observations(request):
    return json.loads(next(field.value for field in request.fields if field.name == "observations"))


class RuntimeToolFailureObservationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.RuntimeDeliveryTests.asyncSetUp

    def runtime(self, replies, outcomes):
        self.container.tool_registry.publish(
            (
                ToolDescriptor(
                    id="fixture.lookup",
                    version="1",
                    extension_id="fixture",
                    risk=RiskLevel.READ,
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                    output_schema={},
                ),
            )
        )
        runtime = build_runtime(self.container)
        provider = ConversationProvider(*replies)
        runtime.planner = StructuredPlanner(ModelRouter((provider,)), provider.provider_id)
        executor = SequenceExecutor(outcomes)
        runtime.tools = ToolGateway(
            registry=runtime.registry,
            policy=ToolPolicy(),
            approvals=runtime.approvals,
            executor=executor,
        )
        return runtime, provider, executor

    async def task(self, key):
        return await self.container.tasks.create(
            objective="Read the configured source", idempotency_key=key
        )

    async def test_real_gateway_failure_reaches_planner_without_raw_error_or_arguments(self):
        private_error = "private path and credentials must not reach the model"
        private_argument = "private-synthetic-query-argument"
        runtime, provider, executor = self.runtime(
            [call(private_argument), ask()],
            [DefinitiveToolFailure(private_error)],
        )
        task = await self.task("failure")
        await runtime.work_once()
        values = observations(provider.requests[1])
        self.assertEqual(1, len(values))
        self.assertIsInstance(values[0], dict)
        self.assertEqual(
            {
                "kind",
                "tool_id",
                "tool_version",
                "error_code",
                "retryable",
                "same_call_as_previous",
                "message",
            },
            set(values[0]),
        )
        self.assertEqual("FAILED", values[0]["kind"])
        self.assertEqual("fixture.lookup", values[0]["tool_id"])
        self.assertEqual("1", values[0]["tool_version"])
        self.assertEqual("TOOL_EXECUTION_FAILED", values[0]["error_code"])
        self.assertFalse(values[0]["retryable"])
        self.assertFalse(values[0]["same_call_as_previous"])
        snapshot = await runtime.checkpoints.load("run_" + task.id)
        self.assertEqual(values, snapshot["observations"])
        for private in (private_error, private_argument):
            self.assertNotIn(private, json.dumps(snapshot))
            self.assertNotIn(private, json.dumps(values))
        self.assertIn("not a successful empty result", provider.requests[1].instruction)
        self.assertIn("NEEDS_USER", provider.requests[1].instruction)
        self.assertIn("same_call_as_previous", provider.requests[1].instruction)
        self.assertEqual(1, len(executor.calls))
        self.assertEqual(TaskState.WAITING_USER, (await runtime.tasks.get(task.id)).state)

    async def test_successful_empty_result_keeps_its_shape_before_a_failure(self):
        empty = {"results": [], "unknown": True, "vector_mode": "disabled"}
        runtime, provider, _ = self.runtime(
            [call("first"), call("second"), ask()],
            [empty, DefinitiveToolFailure("fixture")],
        )
        task = await self.task("empty")
        await runtime.work_once()
        self.assertEqual([empty], observations(provider.requests[1]))
        final = observations(provider.requests[2])
        self.assertEqual(empty, final[0])
        self.assertEqual("FAILED", final[1]["kind"])
        self.assertFalse(final[1]["same_call_as_previous"])
        self.assertEqual(final, (await runtime.checkpoints.load("run_" + task.id))["observations"])

    async def test_identical_failures_still_stop_on_the_third_attempt(self):
        runtime, provider, executor = self.runtime(
            [call()] * 4,
            [DefinitiveToolFailure("fixture")] * 4,
        )
        task = await self.task("same-call")
        await runtime.work_once()
        run = await runtime.runs.get("run_" + task.id)
        self.assertEqual(TaskState.PAUSED_SAFETY, run.state)
        self.assertEqual("SAME_CALL_WITHOUT_PROGRESS", run.pause_reason)
        self.assertEqual(3, run.same_call_without_progress)
        self.assertEqual(3, run.consecutive_errors)
        self.assertEqual(3, len(executor.calls))
        self.assertEqual(3, len(provider.requests))
        snapshot = await runtime.checkpoints.load(run.id)
        self.assertEqual(
            [False, True, True],
            [item["same_call_as_previous"] for item in snapshot["observations"]],
        )

    async def test_changed_arguments_still_stop_after_five_consecutive_errors(self):
        runtime, _, executor = self.runtime(
            [call(str(index)) for index in range(6)],
            [DefinitiveToolFailure("fixture")] * 6,
        )
        task = await self.task("consecutive")
        await runtime.work_once()
        run = await runtime.runs.get("run_" + task.id)
        self.assertEqual(TaskState.PAUSED_SAFETY, run.state)
        self.assertEqual("CONSECUTIVE_ERRORS", run.pause_reason)
        self.assertEqual(5, len(executor.calls))
        snapshot = await runtime.checkpoints.load(run.id)
        self.assertEqual(
            [False] * 5, [item["same_call_as_previous"] for item in snapshot["observations"]]
        )

    async def test_success_without_evidence_still_stops_after_eight_rounds(self):
        runtime, _, executor = self.runtime([call(str(index)) for index in range(9)], [None] * 9)
        task = await self.task("no-evidence")
        await runtime.work_once()
        run = await runtime.runs.get("run_" + task.id)
        self.assertEqual(TaskState.PAUSED_SAFETY, run.state)
        self.assertEqual("NO_PROGRESS_ROUNDS", run.pause_reason)
        self.assertEqual(8, len(executor.calls))
        self.assertEqual([None] * 8, (await runtime.checkpoints.load(run.id))["observations"])

    async def test_followup_starts_new_segment_with_new_user_context(self):
        runtime, provider, executor = self.runtime(
            [call("old"), call("old"), ask("Which term should I use?"), call("new"), ask()],
            [DefinitiveToolFailure("fixture")] * 3,
        )
        task = await self.task("followup")
        await runtime.work_once()
        first = await runtime.runs.get("run_" + task.id)
        self.assertEqual(2, first.same_call_without_progress)
        current = await runtime.tasks.get(task.id)
        current, _ = await runtime.tasks.add_message(
            task_id=task.id,
            content="Search new",
            actor="owner",
            expected_version=current.version,
            idempotency_key="new-term",
        )
        await runtime.request(task.id, version=current.version, mode="agent", key="continue")
        await runtime.work_once()
        run = await runtime.runs.get(first.id)
        self.assertEqual(first.segment_number + 1, run.segment_number)
        self.assertEqual(TaskState.WAITING_USER, run.state)
        self.assertEqual(1, run.consecutive_errors)
        self.assertEqual(1, run.same_call_without_progress)
        self.assertEqual(1, run.no_progress_rounds)
        self.assertEqual({"query": "new"}, executor.calls[-1])
        self.assertEqual(
            [
                {"role": "assistant", "content": "Which term should I use?"},
                {"role": "user", "content": "Search new"},
            ],
            json.loads(messages_field(provider.requests[3]).value),
        )
        self.assertFalse(observations(provider.requests[4])[-1]["same_call_as_previous"])

    async def test_unknown_still_pauses_before_new_observation_or_planning(self):
        runtime, provider, executor = self.runtime(
            [call(), ask()], [OutcomeUnknownError("fixture")]
        )
        task = await self.task("unknown")
        await runtime.work_once()
        self.assertEqual(TaskState.WAITING_RECONCILIATION, (await runtime.tasks.get(task.id)).state)
        self.assertNotIn("observations", await runtime.checkpoints.load("run_" + task.id))
        self.assertEqual(1, len(executor.calls))
        self.assertEqual(1, len(provider.requests))
