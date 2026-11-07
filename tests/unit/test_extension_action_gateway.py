from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.tools import ToolGateway, ToolPolicy, ToolRegistry
from personal_assistant.domain import (
    ApprovalState,
    RiskLevel,
    ToolCall,
    ToolDescriptor,
    ToolOutcomeKind,
)
from personal_assistant.infrastructure.memory.outbox import InMemorySideEffectOutbox
from personal_assistant.infrastructure.tools.capability_router import CapabilityRoutingExecutor
from personal_assistant.infrastructure.tools.extension_actions import ExtensionActionService


class ExternalGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, result=None, error=None, drift=False):
        args = {
            "local_action_id": "fixture-1",
            "target": {"recipient": "fixture"},
            "consequences": "Sends the reviewed text",
            "operation": {"text": "synthetic"},
            "attachments": [],
        }

        class Invoker:
            calls = 0
            changed = False

            async def invoke_tool(self, extension_id, tool_id, arguments, **kwargs):
                if tool_id.endswith(".preview"):
                    output = {
                        "kind": "EXTENSION_ACTION",
                        "payload": copy.deepcopy(arguments),
                        "target": arguments["target"],
                        "consequences": arguments["consequences"],
                        "attachments": [],
                    }
                    if self.changed:
                        output["target"] = {"recipient": "changed"}
                    return {"outcome": "SUCCEEDED", "output": output}
                self.calls += 1
                if error:
                    raise error
                return result

        invoker = Invoker()
        registry = ToolRegistry()
        registry.publish(
            tuple(
                ToolDescriptor(
                    id=id_,
                    version="1",
                    extension_id="fixture",
                    extension_version="1",
                    risk=risk,
                    input_schema={},
                    output_schema={},
                )
                for id_, risk in (
                    ("fixture.external", RiskLevel.EXTERNAL_WRITE),
                    ("fixture.external.preview", RiskLevel.READ),
                )
            )
        )
        service = ExtensionActionService(invoker, registry, None)
        approvals = ApprovalService(InMemoryApprovalRepository())
        outbox = InMemorySideEffectOutbox(approvals)
        gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=approvals,
            outbox=outbox,
            executor=CapabilityRoutingExecutor(extension_router=invoker, extension_actions=service),
        )
        call = await service.prepare_call(
            ToolCall(
                tool_id="fixture.external",
                tool_version="1",
                arguments=args,
                task_id="task1",
                workflow_allowed_tools=frozenset({"fixture.external"}),
            )
        )
        first = await gateway.invoke(call)
        self.assertEqual(ToolOutcomeKind.APPROVAL_REQUIRED, first.kind)
        approval = await approvals.get(first.approval_id)
        await approvals.approve(approval.id, nonce=approval.nonce, actor_id="fixture-user")
        invoker.changed = drift
        approved = replace(call, approval_id=approval.id)
        completed = await gateway.invoke(approved)
        if drift:
            self.assertEqual(ToolOutcomeKind.FAILED, completed.kind)
            self.assertEqual(0, invoker.calls)
            return
        expected = (
            ApprovalState.SUCCEEDED
            if completed.kind is ToolOutcomeKind.SUCCESS
            else ApprovalState.UNKNOWN
        )
        self.assertEqual(expected, (await approvals.get(approval.id)).state)
        self.assertEqual(1, len(outbox._intents))
        self.assertEqual(expected.value, next(iter(outbox._intents.values())).state.value)
        repeated = await gateway.invoke(approved)
        self.assertNotEqual(ToolOutcomeKind.SUCCESS, repeated.kind)
        self.assertEqual(1, invoker.calls)
        return completed.kind

    async def test_worker_timeout_after_dispatch_is_unknown_and_never_retried(self):
        self.assertEqual(ToolOutcomeKind.OUTCOME_UNKNOWN, await self.exercise(error=TimeoutError()))

    async def test_empty_success_receipt_is_unknown_and_never_retried(self):
        self.assertEqual(
            ToolOutcomeKind.OUTCOME_UNKNOWN,
            await self.exercise(result={"outcome": "SUCCEEDED", "output": {}}),
        )

    async def test_worker_error_status_after_dispatch_cannot_prove_zero_writes(self):
        for outcome in ("PERMANENT", "RETRYABLE", "NEEDS_USER_ACTION"):
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    ToolOutcomeKind.OUTCOME_UNKNOWN,
                    await self.exercise(result={"outcome": outcome, "output": {}}),
                )

    async def test_verified_receipt_succeeds_once(self):
        self.assertEqual(
            ToolOutcomeKind.SUCCESS,
            await self.exercise(
                result={
                    "outcome": "SUCCEEDED",
                    "output": {"state": "SENT"},
                    "external_receipt": {"verified": True, "reference_id": "r1"},
                }
            ),
        )

    async def test_material_change_after_approval_never_dispatches(self):
        await self.exercise(drift=True)
