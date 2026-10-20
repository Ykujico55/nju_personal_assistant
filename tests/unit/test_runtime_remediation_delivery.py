from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from personal_assistant.bootstrap import build_container
from personal_assistant.core.jobs.outbox import SideEffectState
from personal_assistant.core.tools import OutcomeUnknownError, ToolGateway, ToolPolicy
from personal_assistant.domain import (
    ApprovalState,
    RiskLevel,
    TaskState,
    ToolDescriptor,
    ValidationError,
)
from personal_assistant.runtime import build_runtime
from personal_assistant.settings import Settings
from tests.unit.test_tool_gateway import FakeExecutor


class RuntimeUnknownRemediationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pa_unknown_delivery_")
        self.addCleanup(self.directory.cleanup)
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()
        settings = replace(settings, extension_root=Path(self.directory.name) / "extensions")
        self.container = build_container(settings)
        self.addAsyncCleanup(self.container.aclose)

    async def generate_unknown_checkpoint(self, *, crash=False):
        descriptor = ToolDescriptor(
            id="fixture.external",
            version="1",
            extension_id="fixture",
            extension_version="1",
            risk=RiskLevel.EXTERNAL_WRITE,
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object"},
        )
        self.container.tool_registry.publish((descriptor,))
        executor = FakeExecutor(error=OutcomeUnknownError("synthetic-unconfirmed-action"))
        gateway = ToolGateway(
            registry=self.container.tool_registry,
            policy=ToolPolicy(),
            approvals=self.container.approvals,
            executor=executor,
            outbox=self.container.side_effect_outbox,
        )
        runtime = build_runtime(self.container)
        runtime.tools = gateway
        runtime.prepare = AsyncMock(
            side_effect=lambda call: replace(call, target={"fixture": "only"})
        )
        runtime.planner.propose = AsyncMock(
            side_effect=[
                {
                    "kind": "CALL_TOOL",
                    "tool_id": descriptor.id,
                    "tool_version": "1",
                    "arguments": {},
                },
                {"kind": "COMPLETE", "message": "must not plan this after UNKNOWN"},
            ]
        )
        task = await self.container.tasks.create(
            objective="synthetic fixture", idempotency_key="create"
        )
        await runtime.work_once()
        run = await self.container.run_repository.get("run_" + task.id)
        self.assertEqual(TaskState.WAITING_APPROVAL, run.state)
        approval = await self.container.approvals.get(run.waiting_reference)
        await self.container.approvals.approve(approval.id, nonce=approval.nonce, actor_id="owner")
        await self.container.jobs.enqueue(
            kind="agent.approval",
            payload={"task_id": task.id, "approval_id": approval.id},
            idempotency_key="approved-dispatch",
        )
        invoke_real = gateway.invoke

        async def lose_receipt(call):
            await invoke_real(call)
            if crash:
                raise asyncio.CancelledError()
            raise RuntimeError("synthetic receipt loss after durable UNKNOWN")

        with patch.object(gateway, "invoke", new=AsyncMock(side_effect=lose_receipt)):
            if crash:
                with self.assertRaises(asyncio.CancelledError):
                    await runtime.work_once()
            else:
                await runtime.work_once()
        final = await self.container.approvals.get(approval.id)
        self.assertEqual(ApprovalState.UNKNOWN, final.state)
        self.assertEqual(1, executor.calls)
        if hasattr(self.container.side_effect_outbox, "_intents"):
            intents = list(self.container.side_effect_outbox._intents.values())
            self.assertEqual([SideEffectState.UNKNOWN], [intent.state for intent in intents])
        else:
            async with self.container.storage.database.connection() as connection:
                rows = await connection.fetch(
                    "SELECT state FROM side_effect_intents WHERE task_id=$1", task.id
                )
                self.assertEqual(["UNKNOWN"], [row["state"] for row in rows])
        snapshot = await self.container.checkpoint_store.load("run_" + task.id)
        self.assertEqual(RiskLevel.EXTERNAL_WRITE.value, snapshot["risk"])
        return runtime, task, executor

    async def assert_resume_blocked(self, runtime, task, executor):
        current = await self.container.tasks.get(task.id)
        before = await self.container.records.get("requests", task.id)
        with self.assertRaises(ValidationError):
            await runtime.request(
                task.id, version=current.version, mode="agent", key="ordinary-continue"
            )
        self.assertEqual(before, await self.container.records.get("requests", task.id))
        self.assertEqual(
            TaskState.WAITING_RECONCILIATION, (await self.container.tasks.get(task.id)).state
        )
        self.assertEqual(1, runtime.planner.propose.await_count)
        self.assertEqual(1, executor.calls)

    async def test_unknown_receipt_loss_cannot_resume_and_replan_to_success(self):
        runtime, task, executor = await self.generate_unknown_checkpoint()
        await self.assert_resume_blocked(runtime, task, executor)

    async def test_crashed_worker_recognizes_actual_persisted_risk_and_never_replays(self):
        runtime, task, executor = await self.generate_unknown_checkpoint(crash=True)
        snapshot = await self.container.checkpoint_store.load("run_" + task.id)
        self.assertEqual("dispatching", snapshot["phase"])
        await runtime.advance(task.id, resume=True)
        await self.assert_resume_blocked(runtime, task, executor)
