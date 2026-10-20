"""Additional local cross-check: known failure is not an unresolved external result."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from personal_assistant.bootstrap import build_container
from personal_assistant.core.tools import DefinitiveToolFailure, ToolGateway, ToolPolicy
from personal_assistant.domain import ApprovalState, RiskLevel, TaskState, ToolDescriptor
from personal_assistant.runtime import build_runtime
from personal_assistant.settings import Settings
from tests.unit.test_tool_gateway import FakeExecutor


class RuntimeKnownFailureCrosscheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_failed_external_receipt_loss_allows_explicit_continue(self):
        with tempfile.TemporaryDirectory(prefix="pa_failed_delivery_") as directory:
            with patch.dict("os.environ", {}, clear=True):
                settings = Settings.from_env()
            container = build_container(
                replace(
                    settings,
                    extension_root=Path(directory) / "extensions",
                )
            )
            try:
                descriptor = ToolDescriptor(
                    id="fixture.external",
                    version="1",
                    extension_id="fixture",
                    extension_version="1",
                    risk=RiskLevel.EXTERNAL_WRITE,
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                )
                container.tool_registry.publish((descriptor,))
                executor = FakeExecutor(error=DefinitiveToolFailure("definitely not sent"))
                gateway = ToolGateway(
                    registry=container.tool_registry,
                    policy=ToolPolicy(),
                    approvals=container.approvals,
                    executor=executor,
                    outbox=container.side_effect_outbox,
                )
                runtime = build_runtime(container)
                runtime.tools = gateway
                runtime.prepare = AsyncMock(
                    side_effect=lambda call: replace(call, target={"fixture": "only"}),
                )
                runtime.planner.propose = AsyncMock(
                    side_effect=[
                        {
                            "kind": "CALL_TOOL",
                            "tool_id": descriptor.id,
                            "tool_version": "1",
                            "arguments": {},
                        },
                        {"kind": "COMPLETE", "message": "已确定没有发送，任务终止。"},
                    ]
                )
                task = await container.tasks.create(objective="synthetic", idempotency_key="c")
                await runtime.work_once()
                run = await container.run_repository.get("run_" + task.id)
                approval = await container.approvals.get(run.waiting_reference)
                await container.approvals.approve(
                    approval.id,
                    nonce=approval.nonce,
                    actor_id="owner",
                )
                await container.jobs.enqueue(
                    kind="agent.approval",
                    payload={"task_id": task.id, "approval_id": approval.id},
                    idempotency_key="approved",
                )
                invoke = gateway.invoke

                async def lose_receipt(call):
                    await invoke(call)
                    raise RuntimeError("receipt lost after definitive durable failure")

                with patch.object(gateway, "invoke", new=AsyncMock(side_effect=lose_receipt)):
                    await runtime.work_once()
                self.assertEqual(
                    ApprovalState.FAILED, (await container.approvals.get(approval.id)).state
                )
                current = await container.tasks.get(task.id)
                self.assertEqual(TaskState.WAITING_USER, current.state)
                await runtime.request(
                    task.id, version=current.version, mode="agent", key="continue"
                )
                await runtime.work_once()
                self.assertEqual(TaskState.SUCCEEDED, (await container.tasks.get(task.id)).state)
                self.assertEqual(1, executor.calls)
            finally:
                await container.aclose()
