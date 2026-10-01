"""F08.5: a prepared R2 action emits only a pending hint, never an action."""

from __future__ import annotations

import unittest

from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.tools import ToolGateway, ToolPolicy, ToolRegistry
from personal_assistant.domain import RiskLevel, ToolCall, ToolDescriptor, ToolOutcomeKind


class _NoExecute:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, *_args: object) -> object:
        self.calls += 1
        raise AssertionError("preparing approval must not execute the tool")


class _PendingNotifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def notify_pending(self, *, task_id: str, risk: str) -> None:
        self.calls.append({"task_id": task_id, "risk": risk})


class WebPushGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_r2_prepare_notifies_after_approval_is_persisted(self) -> None:
        registry = ToolRegistry()
        registry.publish((ToolDescriptor(
            id="example.send", version="1", extension_id="example.extension",
            extension_version="0.1.0", risk=RiskLevel.EXTERNAL_WRITE,
            input_schema={"type": "object", "additionalProperties": True},
            output_schema={"type": "object", "additionalProperties": True},
        ),))
        approvals = ApprovalService(InMemoryApprovalRepository())
        notifier = _PendingNotifier()
        executor = _NoExecute()
        gateway = ToolGateway(
            registry=registry, policy=ToolPolicy(), approvals=approvals,
            executor=executor, pending_notifier=notifier,
        )
        outcome = await gateway.invoke(ToolCall(
            tool_id="example.send", tool_version="1", task_id="task_123",
            arguments={"body": "secret message"}, target={"to": "private@example.test"},
            workflow_allowed_tools=frozenset({"example.send"}),
        ))
        self.assertEqual(ToolOutcomeKind.APPROVAL_REQUIRED, outcome.kind)
        self.assertIsNotNone(await approvals.get(outcome.approval_id or ""))
        self.assertEqual([{"task_id": "task_123", "risk": "R2"}], notifier.calls)
        self.assertEqual(0, executor.calls)
