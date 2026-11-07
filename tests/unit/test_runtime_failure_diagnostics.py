"""Synthetic failures exercise actual queue/run/checkpoint diagnostic boundaries."""

from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from unittest.mock import AsyncMock, patch

from personal_assistant.core.agent.planner import StructuredPlanner
from personal_assistant.core.conversation import answer_request, routing_request
from personal_assistant.core.extensions.config import ExtensionConfigError
from personal_assistant.core.extensions.errors import ExtensionOperationError
from personal_assistant.core.jobs.queue import JobState
from personal_assistant.core.models.errors import (
    ModelCredentialUnavailableError,
    ModelOutputFilteredError,
    ModelOutputTruncatedError,
    ModelProviderProtocolError,
    ModelProviderRejectedError,
    ModelProviderTimeoutError,
    ModelProviderUnavailableError,
)
from personal_assistant.core.secrets.store import SecretUnavailableError
from personal_assistant.core.tools import ExtensionUnavailableError, ToolGateway, ToolPolicy
from personal_assistant.domain import TaskRun, TaskState
from personal_assistant.runtime import build_runtime
from tests.unit import test_runtime_delivery as fixtures
from tests.unit.test_planner_validation_diagnostics import ReplyRouter
from tests.unit.test_tool_gateway import FakeExecutor

SENTINEL = "private-synthetic-api-key-and-document-body"


class RuntimeFailureDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.RuntimeDeliveryTests.asyncSetUp

    async def test_typed_failures_have_safe_code_and_correlated_terminal_records(self):
        cases = (
            (ModelCredentialUnavailableError(SENTINEL), "MODEL_CREDENTIAL_UNAVAILABLE"),
            (ModelProviderRejectedError(SENTINEL, status_code=429), "MODEL_PROVIDER_REJECTED"),
            (ModelProviderTimeoutError(SENTINEL), "MODEL_PROVIDER_TIMEOUT"),
            (ModelProviderUnavailableError(SENTINEL), "MODEL_PROVIDER_UNAVAILABLE"),
            (ModelProviderProtocolError(SENTINEL), "MODEL_PROVIDER_PROTOCOL_ERROR"),
            (ModelOutputTruncatedError(SENTINEL), "MODEL_OUTPUT_TRUNCATED"),
            (ModelOutputFilteredError(SENTINEL), "MODEL_OUTPUT_FILTERED"),
            (ExtensionConfigError(SENTINEL), "EXTENSION_CONFIGURATION_FAILED"),
            (ExtensionOperationError("EXTENSION_NOT_ENABLED", SENTINEL), "EXTENSION_UNAVAILABLE"),
            (SecretUnavailableError(SENTINEL), "SECRET_UNAVAILABLE"),
            (RuntimeError(SENTINEL), "TASK_DISPATCH_FAILED"),
        )
        seen = set()
        for number, (failure, code) in enumerate(cases):
            with self.subTest(code=code):
                task = await self.container.tasks.create(
                    objective="synthetic", idempotency_key=str(number)
                )
                runtime = build_runtime(self.container)
                runtime.planner.propose = AsyncMock(side_effect=failure)
                self.assertTrue(await runtime.work_once())
                snapshot = await runtime.checkpoints.load("run_" + task.id)
                diagnostic = snapshot.get("failure_diagnostic")
                self.assertIsInstance(diagnostic, dict)
                self.assertEqual(code, diagnostic["code"])
                correlation = diagnostic["correlation_id"]
                self.assertRegex(correlation, r"^diag_[0-9a-f]{32}$")
                self.assertNotIn(correlation, seen)
                seen.add(correlation)
                record = await self.container.records.get("executions", task.id)
                self.assertEqual(diagnostic, record["value"]["failure_diagnostic"])
                self.assertEqual(code, record["value"]["error_code"])
                messages = [m.content for m in await runtime.tasks.messages(task.id)]
                self.assertIn(correlation, messages[-1])
                self.assertEqual(TaskState.WAITING_USER, (await runtime.tasks.get(task.id)).state)
                jobs = list(self.container.jobs._jobs.values())
                job = next(j for j in jobs if j.payload.get("task_id") == task.id)
                self.assertEqual(JobState.FAILED, job.state)
                self.assertEqual(code, job.error_code)
                self.assertFalse(await runtime.work_once())
                rendered = json.dumps([snapshot, record, messages, asdict(job)], default=str)
                self.assertNotIn(SENTINEL, rendered)

    async def test_gateway_extension_unavailable_has_safe_waiting_diagnostic(self):
        descriptor = fixtures.DecisionDeliveryTests().descriptor()
        self.container.tool_registry.publish((descriptor,))
        task = await self.container.tasks.create(objective="synthetic", idempotency_key="gateway")
        runtime = build_runtime(self.container)
        executor = FakeExecutor(error=ExtensionUnavailableError("fixture", SENTINEL))
        runtime.tools = ToolGateway(
            registry=runtime.registry,
            policy=ToolPolicy(),
            approvals=runtime.approvals,
            executor=executor,
        )
        runtime.planner.propose = AsyncMock(
            return_value={
                "kind": "CALL_TOOL",
                "tool_id": descriptor.id,
                "tool_version": descriptor.version,
                "arguments": {},
            }
        )
        await runtime.work_once()
        snapshot = await runtime.checkpoints.load("run_" + task.id)
        self.assertEqual("EXTENSION_UNAVAILABLE", snapshot["failure_diagnostic"]["code"])
        record = await runtime.records.get("executions", task.id)
        messages = [m.content for m in await runtime.tasks.messages(task.id)]
        self.assertNotIn(SENTINEL, json.dumps([snapshot, record, messages], default=str))
        self.assertEqual(TaskState.PAUSED_EXTENSION, (await runtime.tasks.get(task.id)).state)
        self.assertEqual(1, executor.calls)
        self.assertFalse(await runtime.work_once())

    async def test_missing_tool_preserves_validation_state_and_has_correlation(self):
        descriptor = fixtures.DecisionDeliveryTests().descriptor()
        self.container.tool_registry.publish((descriptor,))
        task = await self.container.tasks.create(objective="synthetic", idempotency_key="missing")
        runtime = build_runtime(self.container)

        async def propose(**kwargs):
            self.container.tool_registry.publish(())
            return {
                "kind": "CALL_TOOL",
                "tool_id": descriptor.id,
                "tool_version": descriptor.version,
                "arguments": {},
            }

        runtime.planner.propose = AsyncMock(side_effect=propose)
        runtime.tools.invoke = AsyncMock()
        await runtime.work_once()
        snapshot = await runtime.checkpoints.load("run_" + task.id)
        self.assertEqual("TOOL_UNAVAILABLE", snapshot["failure_diagnostic"]["code"])
        self.assertEqual("HOST_RESOURCE_UNAVAILABLE", snapshot["validation_failure"]["code"])
        self.assertEqual(TaskState.WAITING_USER, (await runtime.tasks.get(task.id)).state)
        runtime.tools.invoke.assert_not_awaited()

    async def test_startup_failure_preserves_orphan_unknown_checkpoint_and_reports_safe_id(self):
        task = await self.container.tasks.create(objective="synthetic", idempotency_key="orphan")
        runtime = build_runtime(self.container)
        run = TaskRun(id="run_" + task.id, task_id=task.id, objective=task.objective)
        snapshot = {"unresolved_external": True, "phase": "dispatching"}
        await runtime.checkpoints.save(run, snapshot)
        snapshot = await runtime.checkpoints.load(run.id)
        with patch.object(
            runtime.runs, "add", new=AsyncMock(side_effect=SecretUnavailableError(SENTINEL))
        ):
            await runtime.work_once()
        self.assertEqual(snapshot, await runtime.checkpoints.load(run.id))
        self.assertEqual(TaskState.WAITING_RECONCILIATION, (await runtime.tasks.get(task.id)).state)
        content = (await runtime.tasks.messages(task.id))[-1].content
        self.assertRegex(content, r"diag_[0-9a-f]{32}")
        self.assertIn("SECRET_UNAVAILABLE", content)
        self.assertNotIn(SENTINEL, content)
        self.assertFalse(await runtime.work_once())

    async def test_successful_explicit_resume_clears_old_failure_projection(self):
        task = await self.container.tasks.create(objective="synthetic", idempotency_key="resume")
        runtime = build_runtime(self.container)
        runtime.planner.propose = AsyncMock(side_effect=ModelProviderTimeoutError(SENTINEL))
        await runtime.work_once()
        failed = await runtime.checkpoints.load("run_" + task.id)
        self.assertIn("failure_diagnostic", failed)
        current = await runtime.tasks.get(task.id)
        await runtime.request(task.id, version=current.version, mode="agent", key="continue")
        runtime.planner.propose = AsyncMock(return_value={"kind": "COMPLETE", "message": "done"})
        await runtime.work_once()
        snapshot = await runtime.checkpoints.load("run_" + task.id)
        self.assertNotIn("failure_diagnostic", snapshot)
        record = await runtime.records.get("executions", task.id)
        self.assertIsNone(record["value"].get("failure_diagnostic"))
        self.assertIsNone(record["value"]["error_code"])

    async def test_prompts_do_not_infer_missing_configuration_from_unavailable_tools(self):
        router = ReplyRouter(['{"kind":"NEEDS_USER","message":"check tools"}'])
        planner = StructuredPlanner(router, "fixture")
        await planner.propose(
            objective="synthetic", messages=[], tools=(), observations=[], rules={}
        )
        requests = [
            router.requests[0],
            routing_request("synthetic", [], [], []),
            answer_request("synthetic", []),
        ]
        for request in requests:
            self.assertIn("missing directory configuration", request.instruction)
            self.assertIn("do not ask", request.instruction.lower())

    def test_diagnostic_projection_rebuilds_only_known_fields(self):
        from personal_assistant.core.agent.failure_diagnostics import failure_diagnostic

        correlation = "diag_" + "a" * 32
        result = failure_diagnostic(
            {
                "code": "MODEL_PROVIDER_TIMEOUT",
                "correlation_id": correlation,
                "message": SENTINEL,
                "body": SENTINEL,
            }
        )
        self.assertEqual({"code", "message", "correlation_id"}, set(result))
        self.assertNotIn(SENTINEL, json.dumps(result))
        for value in (
            {"code": SENTINEL, "correlation_id": correlation},
            {"code": "MODEL_PROVIDER_TIMEOUT", "correlation_id": SENTINEL},
            None,
        ):
            self.assertIsNone(failure_diagnostic(value))
