"""Original action -> authoritative host ledger -> explicit recovery, in disposable PG."""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.core.mail.ports import MailDeliveryRecord, MailDeliveryStatus
from personal_assistant.core.tools import OutcomeUnknownError, ToolGateway, ToolPolicy
from personal_assistant.domain import (
    ApprovalState,
    ConcurrentModificationError,
    RiskLevel,
    TaskState,
    ToolDescriptor,
    ValidationError,
)
from personal_assistant.runtime import build_runtime
from tests.integration import test_postgres_delivery as postgres
from tests.unit.test_tool_gateway import FakeExecutor


@unittest.skipUnless(postgres.BASE, "set PA_TEST_DATABASE_URL for disposable PostgreSQL")
class TaskReconciliationPostgresTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = postgres.PostgresDeliveryTests.asyncSetUp
    asyncTearDown = postgres.PostgresDeliveryTests.asyncTearDown

    async def prepared(self):
        c = self.containers[0]
        args = {
            "local_action_id": "synthetic-action",
            "account_id": "synthetic-account",
            "message_id": "<reconcile@example.invalid>",
            "mime_sha256": "a" * 64,
        }
        descriptor = ToolDescriptor(
            id="fixture.external",
            version="1",
            extension_id="fixture",
            extension_version="1",
            risk=RiskLevel.EXTERNAL_WRITE,
            required_capabilities=("mail.send",),
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        c.tool_registry.publish((descriptor,))
        executor = FakeExecutor(error=OutcomeUnknownError(args["local_action_id"]))
        runtime = build_runtime(c)
        runtime.grants = {descriptor.id: frozenset({"mail.send"})}
        runtime.tools = ToolGateway(
            registry=c.tool_registry,
            policy=ToolPolicy(),
            approvals=c.approvals,
            executor=executor,
            outbox=c.side_effect_outbox,
        )
        runtime.prepare = AsyncMock(
            side_effect=lambda call: replace(
                call,
                target={"account_id": args["account_id"]},
                idempotency_key=args["local_action_id"],
            )
        )
        runtime.planner.propose = AsyncMock(
            side_effect=[
                {
                    "kind": "CALL_TOOL",
                    "tool_id": descriptor.id,
                    "tool_version": "1",
                    "arguments": args,
                },
                {"kind": "COMPLETE", "message": "verified result consumed"},
            ]
        )
        task = await c.tasks.create(objective="synthetic reconciliation", idempotency_key="create")
        await runtime.work_once()
        run = await c.run_repository.get("run_" + task.id)
        approval = await c.approvals.get(run.waiting_reference)
        await c.approvals.approve(approval.id, nonce=approval.nonce, actor_id="owner")
        await c.jobs.enqueue(
            kind="agent.approval",
            payload={"task_id": task.id, "approval_id": approval.id},
            idempotency_key="approval-dispatch",
        )
        await runtime.work_once()
        task = await c.tasks.get(task.id)
        self.assertEqual(TaskState.WAITING_RECONCILIATION, task.state)
        self.assertEqual(1, executor.calls)
        record = MailDeliveryRecord(
            local_action_id=args["local_action_id"],
            account_id=args["account_id"],
            message_id=args["message_id"],
            status=MailDeliveryStatus.PREPARED,
            envelope_digest=canonical_sha256(args),
            mime_sha256=args["mime_sha256"],
        )
        await c.mail_ledger.prepare(record)
        await c.mail_ledger.begin_execution(
            record.local_action_id, record.envelope_digest, owner_id="fixture", lease_seconds=30
        )
        await c.mail_ledger.finalize(
            record.local_action_id, record.envelope_digest, status=MailDeliveryStatus.UNKNOWN
        )
        await c.mail_ledger.reconcile(
            record.local_action_id,
            account_id=record.account_id,
            message_id=record.message_id,
            status=MailDeliveryStatus.SUCCEEDED,
            diagnostic_code="SENT_RECONCILED",
        )
        return c, runtime, task, executor, approval

    async def test_host_receipt_unblocks_once_without_resending_after_reconnect(self):
        c, runtime, task, executor, approval = await self.prepared()
        with self.assertRaises(ValidationError):
            await runtime.request(task.id, version=task.version, mode="agent", key="plain")
        result = await runtime.reconcile(task.id, version=task.version, key="resolve")
        self.assertTrue(result["resolved"])
        self.assertEqual("WAITING_USER", result["state"])
        reopened = build_runtime(c)
        self.assertEqual(
            result, await reopened.reconcile(task.id, version=task.version, key="resolve")
        )
        self.assertEqual(ApprovalState.UNKNOWN, (await c.approvals.get(approval.id)).state)
        async with c.storage.database.connection() as con:
            self.assertEqual(
                "UNKNOWN",
                await con.fetchval(
                    "SELECT state FROM side_effect_intents WHERE approval_id=$1", approval.id
                ),
            )
        self.assertEqual(1, executor.calls)
        self.assertFalse(await runtime.work_once())
        current = await c.tasks.get(task.id)
        await runtime.request(task.id, version=current.version, mode="agent", key="after-proof")
        await runtime.work_once()
        self.assertEqual(TaskState.SUCCEEDED, (await c.tasks.get(task.id)).state)
        self.assertEqual(1, executor.calls)

    async def test_resolution_failure_rolls_back_all_mutations_and_can_retry_original_key(self):
        c, runtime, task, executor, _ = await self.prepared()
        before = await c.checkpoint_store.load("run_" + task.id)
        real_save = c.checkpoint_store.save

        async def fail_after_save(*args, **kwargs):
            await real_save(*args, **kwargs)
            raise RuntimeError("synthetic checkpoint commit interruption")

        with (
            patch.object(c.checkpoint_store, "save", side_effect=fail_after_save),
            self.assertRaises(RuntimeError),
        ):
            await runtime.reconcile(task.id, version=task.version, key="resolve")
        self.assertEqual(task, await c.tasks.get(task.id))
        self.assertEqual(before, await c.checkpoint_store.load("run_" + task.id))
        async with c.storage.database.connection() as con:
            self.assertEqual(
                0,
                await con.fetchval(
                    "SELECT count(*) FROM runtime_commands WHERE command_key='resolve'"
                ),
            )
        self.assertTrue(
            (await runtime.reconcile(task.id, version=task.version, key="resolve"))["resolved"]
        )
        self.assertEqual(1, executor.calls)

    async def test_concurrent_commands_only_one_can_apply_the_original_task_version(self):
        c, runtime, task, executor, _ = await self.prepared()
        results = await asyncio.gather(
            runtime.reconcile(task.id, version=task.version, key="first"),
            build_runtime(c).reconcile(task.id, version=task.version, key="second"),
            return_exceptions=True,
        )
        successes = [r for r in results if isinstance(r, dict) and r.get("resolved")]
        self.assertEqual(1, len(successes), results)
        self.assertTrue(
            any(isinstance(r, (ConcurrentModificationError, ValidationError)) for r in results)
        )
        self.assertEqual(1, executor.calls)
        self.assertEqual(TaskState.WAITING_USER, (await c.tasks.get(task.id)).state)
