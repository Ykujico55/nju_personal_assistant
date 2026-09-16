from __future__ import annotations

import unittest
from typing import Any

from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.tools import OutcomeUnknownError, ToolGateway, ToolPolicy, ToolRegistry
from personal_assistant.domain import (
    ApprovalState,
    RiskLevel,
    ToolCall,
    ToolDescriptor,
    ToolOutcomeKind,
)


class FakeExecutor:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = {} if result is None else result
        self.error = error
        self.calls = 0

    async def execute(self, descriptor: object, arguments: object, context: object) -> Any:
        del descriptor, arguments, context
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def descriptor(risk: RiskLevel) -> ToolDescriptor:
    return ToolDescriptor(
        id="test.action",
        version="1",
        extension_id="test.extension",
        extension_version="0.1.0",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        risk=risk,
    )


def call(
    *,
    approval_id: str | None = None,
    payload: dict[str, object] | None = None,
    idempotency_key: str | None = None,
) -> ToolCall:
    return ToolCall(
        tool_id="test.action",
        tool_version="1",
        task_id="task-1",
        arguments=payload or {"value": 1},
        target={"recipient": "target-1"},
        workflow_allowed_tools=frozenset({"test.action"}),
        approval_id=approval_id,
        idempotency_key=idempotency_key,
    )


class ToolGatewayTests(unittest.IsolatedAsyncioTestCase):
    def make_gateway(self, risk: RiskLevel, executor: FakeExecutor, *, outbox=None):
        registry = ToolRegistry()
        registry.publish((descriptor(risk),))
        repository = InMemoryApprovalRepository()
        approvals = ApprovalService(repository)
        gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=approvals,
            executor=executor,
            outbox=outbox,
        )
        return gateway, approvals

    async def test_r3_never_reaches_executor(self) -> None:
        executor = FakeExecutor()
        gateway, _ = self.make_gateway(RiskLevel.PROHIBITED, executor)
        outcome = await gateway.invoke(call())
        self.assertEqual(ToolOutcomeKind.FAILED, outcome.kind)
        self.assertIn("prohibited_tool", outcome.message or "")
        self.assertEqual(0, executor.calls)

    async def test_r2_requires_then_consumes_exact_approval(self) -> None:
        executor = FakeExecutor({"sent": True})
        gateway, approvals = self.make_gateway(RiskLevel.EXTERNAL_WRITE, executor)
        needed = await gateway.invoke(call())
        self.assertEqual(ToolOutcomeKind.APPROVAL_REQUIRED, needed.kind)
        self.assertEqual(0, executor.calls)
        prepared = await approvals.get(needed.approval_id or "")
        await approvals.approve(prepared.id, nonce=prepared.nonce, actor_id="owner")
        outcome = await gateway.invoke(call(approval_id=prepared.id))
        self.assertEqual(ToolOutcomeKind.SUCCESS, outcome.kind)
        self.assertEqual(1, executor.calls)
        final = await approvals.get(prepared.id)
        self.assertEqual(ApprovalState.SUCCEEDED, final.state)

    async def test_memory_outbox_enforces_single_use_approval(self) -> None:
        from personal_assistant.infrastructure.memory import InMemorySideEffectOutbox

        executor = FakeExecutor({"sent": True})
        registry = ToolRegistry()
        registry.publish((descriptor(RiskLevel.EXTERNAL_WRITE),))
        approvals = ApprovalService(InMemoryApprovalRepository())
        outbox = InMemorySideEffectOutbox(approvals)
        gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=approvals,
            executor=executor,
            outbox=outbox,
        )
        needed = await gateway.invoke(call())
        prepared = await approvals.get(needed.approval_id or "")
        await approvals.approve(prepared.id, nonce=prepared.nonce, actor_id="owner")

        first = await gateway.invoke(
            call(approval_id=prepared.id, idempotency_key="send-1")
        )
        self.assertEqual(ToolOutcomeKind.SUCCESS, first.kind)
        self.assertEqual(1, executor.calls)
        self.assertEqual(ApprovalState.SUCCEEDED, (await approvals.get(prepared.id)).state)

        # The consumed approval must not execute again under a new idempotency key.
        second = await gateway.invoke(
            call(approval_id=prepared.id, idempotency_key="send-2")
        )
        self.assertNotEqual(ToolOutcomeKind.SUCCESS, second.kind)
        self.assertEqual(1, executor.calls)

    async def test_memory_outbox_burns_drifted_approval(self) -> None:
        from personal_assistant.infrastructure.memory import InMemorySideEffectOutbox

        executor = FakeExecutor({"sent": True})
        registry = ToolRegistry()
        registry.publish((descriptor(RiskLevel.EXTERNAL_WRITE),))
        approvals = ApprovalService(InMemoryApprovalRepository())
        outbox = InMemorySideEffectOutbox(approvals)
        gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=approvals,
            executor=executor,
            outbox=outbox,
        )
        needed = await gateway.invoke(call())
        prepared = await approvals.get(needed.approval_id or "")
        await approvals.approve(prepared.id, nonce=prepared.nonce, actor_id="owner")

        drifted = await gateway.invoke(
            call(
                approval_id=prepared.id,
                payload={"value": 999},
                idempotency_key="send-drift",
            )
        )
        self.assertNotEqual(ToolOutcomeKind.SUCCESS, drifted.kind)
        self.assertEqual(0, executor.calls)
        self.assertEqual(ApprovalState.CANCELLED, (await approvals.get(prepared.id)).state)

    async def test_unknown_external_result_is_not_retryable(self) -> None:
        executor = FakeExecutor(error=OutcomeUnknownError("smtp:unknown"))
        gateway, approvals = self.make_gateway(RiskLevel.EXTERNAL_WRITE, executor)
        needed = await gateway.invoke(call())
        prepared = await approvals.get(needed.approval_id or "")
        await approvals.approve(prepared.id, nonce=prepared.nonce, actor_id="owner")
        outcome = await gateway.invoke(call(approval_id=prepared.id))
        self.assertEqual(ToolOutcomeKind.OUTCOME_UNKNOWN, outcome.kind)
        self.assertFalse(outcome.retryable)
        self.assertEqual(ApprovalState.UNKNOWN, (await approvals.get(prepared.id)).state)


if __name__ == "__main__":
    unittest.main()

