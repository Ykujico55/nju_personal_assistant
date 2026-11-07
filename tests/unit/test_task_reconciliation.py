from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from personal_assistant.domain import (
    ApprovalState,
    ConcurrentModificationError,
    TaskState,
    ValidationError,
)
from tests.unit import test_runtime_remediation_delivery as fixture


class TaskReconciliationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.RuntimeUnknownRemediationTests.asyncSetUp
    generate_unknown_checkpoint = fixture.RuntimeUnknownRemediationTests.generate_unknown_checkpoint
    assert_resume_blocked = fixture.RuntimeUnknownRemediationTests.assert_resume_blocked

    async def ready(self):
        runtime, task, executor = await self.generate_unknown_checkpoint()
        snapshot = await runtime.checkpoints.load("run_" + task.id)
        approval_id = snapshot["pending_call"]["approval_id"]
        self.approval_before = await runtime.approvals.get(approval_id)
        self.before_snapshot = snapshot
        runtime.reconciliation = AsyncMock()

        async def proof(call, **identity):
            from personal_assistant.core.agent.reconciliation import (
                ReconciledAction,
                reconciliation_binding,
            )

            return ReconciledAction(
                call_binding=reconciliation_binding(call, **identity),
                receipt={"source": "synthetic_authoritative_ledger", "reference": "matched-1"},
                result={"status": "SUCCEEDED", "receipt_id": "matched-1"},
            )

        runtime.reconciliation.resolve.side_effect = proof
        return runtime, await runtime.tasks.get(task.id), executor

    async def test_original_unknown_requires_explicit_proof_then_continue_without_redispatch(self):
        runtime, task, executor = await self.ready()
        await self.assert_resume_blocked(runtime, task, executor)
        response = await runtime.reconcile(task.id, version=task.version, key="reconcile")
        self.assertTrue(response["resolved"])
        self.assertEqual("WAITING_USER", response["state"])
        self.assertEqual(self.approval_before, await runtime.approvals.get(self.approval_before.id))
        self.assertEqual(ApprovalState.UNKNOWN, self.approval_before.state)
        snapshot = await runtime.checkpoints.load("run_" + task.id)
        self.assertFalse(await runtime.requires_reconciliation(snapshot))
        self.assertNotIn("pending_call", snapshot)
        facts = await runtime.records.list("reconciliations")
        self.assertEqual(1, len(facts))
        self.assertEqual(self.before_snapshot["pending_call"], facts[0]["value"]["call"])
        self.assertEqual("matched-1", snapshot["observations"][-1]["receipt_id"])
        self.assertFalse(await runtime.work_once())
        self.assertEqual(1, executor.calls)
        self.assertEqual(
            response, await runtime.reconcile(task.id, version=task.version, key="reconcile")
        )
        runtime.reconciliation.resolve.assert_awaited_once()
        current = await runtime.tasks.get(task.id)
        await runtime.request(
            task.id, version=current.version, mode="agent", key="continue-after-proof"
        )
        await runtime.work_once()
        self.assertEqual(TaskState.SUCCEEDED, (await runtime.tasks.get(task.id)).state)
        self.assertEqual(1, executor.calls)

    async def test_absent_proof_preserves_checkpoint_and_same_key_receipt(self):
        runtime, task, executor = await self.ready()
        runtime.reconciliation.resolve.side_effect = None
        runtime.reconciliation.resolve.return_value = None
        response = await runtime.reconcile(task.id, version=task.version, key="no-proof")
        self.assertEqual(
            {
                "task_id": task.id,
                "state": "WAITING_RECONCILIATION",
                "resolved": False,
                "version": task.version,
            },
            response,
        )
        self.assertEqual(self.before_snapshot, await runtime.checkpoints.load("run_" + task.id))
        self.assertEqual((), await runtime.records.list("reconciliations"))
        runtime.reconciliation.resolve.side_effect = AssertionError("receipt must replay")
        self.assertEqual(
            response, await runtime.reconcile(task.id, version=task.version, key="no-proof")
        )
        await self.assert_resume_blocked(runtime, task, executor)

    async def test_wrong_binding_rejected_without_state_change(self):
        runtime, task, executor = await self.ready()
        from personal_assistant.core.agent.reconciliation import ReconciledAction

        runtime.reconciliation.resolve.side_effect = None
        runtime.reconciliation.resolve.return_value = ReconciledAction(
            "wrong", {"proof": "wrong"}, {}
        )
        with self.assertRaises(ValidationError):
            await runtime.reconcile(task.id, version=task.version, key="wrong-proof")
        await self.assert_resume_blocked(runtime, task, executor)
        self.assertEqual(self.before_snapshot, await runtime.checkpoints.load("run_" + task.id))

    async def test_version_and_command_identity_are_strict(self):
        runtime, task, executor = await self.ready()
        with self.assertRaises(ConcurrentModificationError):
            await runtime.reconcile(task.id, version=task.version - 1, key="stale")
        await runtime.reconcile(task.id, version=task.version, key="valid")
        with self.assertRaises(ConcurrentModificationError):
            await runtime.reconcile(task.id, version=task.version + 1, key="valid")
        self.assertEqual(1, executor.calls)

    async def test_changed_extension_keeps_unknown(self):
        runtime, task, executor = await self.ready()
        descriptor = runtime.registry.snapshot().resolve("fixture.external", "1")
        runtime.registry.publish((replace(descriptor, extension_version="2"),))
        with self.assertRaises(ValidationError):
            await runtime.reconcile(task.id, version=task.version, key="changed")
        runtime.reconciliation.resolve.assert_not_awaited()
        await self.assert_resume_blocked(runtime, task, executor)

    async def test_missing_resolver_does_not_relax_unknown(self):
        runtime, task, executor = await self.ready()
        runtime.reconciliation = None
        with self.assertRaises(ValidationError):
            await runtime.reconcile(task.id, version=task.version, key="unsupported")
        await self.assert_resume_blocked(runtime, task, executor)

    async def test_concurrent_user_cancel_wins_before_projection(self):
        runtime, task, executor = await self.ready()
        original = runtime.reconciliation.resolve.side_effect

        async def cancel_during_read(call, **identity):
            await runtime.tasks.cancel(
                task_id=task.id,
                expected_version=task.version,
                actor="owner",
                idempotency_key="cancel",
            )
            return await original(call, **identity)

        runtime.reconciliation.resolve.side_effect = cancel_during_read
        with self.assertRaises(ConcurrentModificationError):
            await runtime.reconcile(task.id, version=task.version, key="cancel-race")
        self.assertEqual(TaskState.CANCELLED, (await runtime.tasks.get(task.id)).state)
        self.assertEqual(self.before_snapshot, await runtime.checkpoints.load("run_" + task.id))
        self.assertEqual((), await runtime.records.list("reconciliations"))
        self.assertEqual(1, executor.calls)

    async def test_parallel_commands_accept_one_recovery(self):
        runtime, task, executor = await self.ready()
        results = await asyncio.gather(
            *(runtime.reconcile(task.id, version=task.version, key=key) for key in ("one", "two")),
            return_exceptions=True,
        )
        self.assertEqual(1, sum(isinstance(result, dict) for result in results))
        self.assertEqual(
            1, sum(isinstance(result, ConcurrentModificationError) for result in results)
        )
        self.assertEqual(1, len(await runtime.records.list("reconciliations")))
        self.assertEqual(1, executor.calls)

    async def test_workflow_success_advances_original_step_once_without_another_send(self):
        from personal_assistant.core.agent.reconciliation import (
            ReconciledAction,
            reconciliation_binding,
        )
        from personal_assistant.core.tools import OutcomeUnknownError, ToolGateway, ToolPolicy
        from personal_assistant.domain import RiskLevel, ToolDescriptor
        from personal_assistant.runtime import build_runtime
        from tests.unit.test_tool_gateway import FakeExecutor

        descriptor = ToolDescriptor(
            id="fixture.external",
            version="1",
            extension_id="fixture",
            extension_version="1",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            risk=RiskLevel.EXTERNAL_WRITE,
        )
        self.container.tool_registry.publish((descriptor,))
        executor = FakeExecutor(error=OutcomeUnknownError("workflow-original-unknown"))
        runtime = build_runtime(self.container)
        runtime.tools = ToolGateway(
            registry=runtime.registry,
            policy=ToolPolicy(),
            approvals=runtime.approvals,
            executor=executor,
            outbox=self.container.side_effect_outbox,
        )
        runtime.prepare = AsyncMock(
            side_effect=lambda call: replace(call, target={"fixture": "only"})
        )
        workflow = {
            "id": "fixture.flow",
            "version": "1",
            "steps": [
                {
                    "id": "send",
                    "type": "tool",
                    "tool_id": descriptor.id,
                    "tool_version": "1",
                    "arguments": {},
                },
                {"id": "done", "type": "archive"},
            ],
        }
        task = await runtime.tasks.create(
            objective="fixture workflow", idempotency_key="create-flow"
        )
        await runtime.request(
            task.id, version=task.version, mode="workflow", key="flow", workflow=workflow
        )
        await runtime.work_once()
        run = await runtime.runs.get("run_" + task.id)
        approval = await runtime.approvals.get(run.waiting_reference)
        await runtime.approvals.approve(approval.id, nonce=approval.nonce, actor_id="owner")
        await runtime.jobs.enqueue(
            kind="agent.approval",
            payload={"task_id": task.id, "approval_id": approval.id},
            idempotency_key="approve-flow",
        )
        await runtime.work_once()
        snapshot = await runtime.checkpoints.load(run.id)
        self.assertEqual(0, snapshot["cursor"])
        self.assertEqual("send", snapshot["step_id"])
        runtime.reconciliation = AsyncMock()
        runtime.reconciliation.resolve.side_effect = lambda call, **identity: ReconciledAction(
            reconciliation_binding(call, **identity),
            {"reference": "flow-host-proof"},
            {"status": "SUCCEEDED", "reference": "flow-host-proof"},
        )
        task = await runtime.tasks.get(task.id)
        response = await runtime.reconcile(task.id, version=task.version, key="flow-proof")
        self.assertEqual(
            response, await runtime.reconcile(task.id, version=task.version, key="flow-proof")
        )
        snapshot = await runtime.checkpoints.load(run.id)
        self.assertEqual(1, snapshot["cursor"])
        self.assertEqual("flow-host-proof", snapshot["outputs"]["send"]["reference"])
        self.assertEqual(1, len(snapshot["observations"]))
        self.assertFalse(await runtime.work_once())
        task = await runtime.tasks.get(task.id)
        await runtime.request(
            task.id, version=task.version, mode="workflow", key="flow-continue", workflow=workflow
        )
        await runtime.work_once()
        self.assertEqual(TaskState.SUCCEEDED, (await runtime.tasks.get(task.id)).state)
        self.assertEqual(1, executor.calls)
        self.assertEqual(ApprovalState.UNKNOWN, (await runtime.approvals.get(approval.id)).state)

    async def test_proof_read_exception_never_clears_waiting(self):
        runtime, task, executor = await self.ready()
        runtime.reconciliation.resolve.side_effect = RuntimeError("synthetic ledger outage")
        with self.assertRaises(RuntimeError):
            await runtime.reconcile(task.id, version=task.version, key="unavailable")
        self.assertEqual(self.before_snapshot, await runtime.checkpoints.load("run_" + task.id))
        await self.assert_resume_blocked(runtime, task, executor)

    async def test_concurrent_message_invalidates_confirmation_version(self):
        runtime, task, executor = await self.ready()
        original = runtime.reconciliation.resolve.side_effect

        async def message_during_read(call, **identity):
            await runtime.tasks.add_message(
                task_id=task.id,
                content="new input",
                expected_version=task.version,
                actor="owner",
                idempotency_key="new-message",
            )
            return await original(call, **identity)

        runtime.reconciliation.resolve.side_effect = message_during_read
        with self.assertRaises(ConcurrentModificationError):
            await runtime.reconcile(task.id, version=task.version, key="message-race")
        self.assertEqual(self.before_snapshot, await runtime.checkpoints.load("run_" + task.id))
        self.assertEqual((), await runtime.records.list("reconciliations"))
        self.assertEqual(1, executor.calls)

    async def test_stale_approval_job_cannot_resume_after_reconciliation(self):
        runtime, task, executor = await self.ready()
        await runtime.reconcile(task.id, version=task.version, key="proof-before-stale-job")
        await runtime.jobs.enqueue(
            kind="agent.approval",
            payload={"task_id": task.id, "approval_id": self.approval_before.id},
            idempotency_key="old-approval-delivery",
        )
        self.assertTrue(await runtime.work_once())
        self.assertEqual(TaskState.WAITING_USER, (await runtime.tasks.get(task.id)).state)
        self.assertEqual(1, runtime.planner.propose.await_count)
        self.assertEqual(1, executor.calls)
