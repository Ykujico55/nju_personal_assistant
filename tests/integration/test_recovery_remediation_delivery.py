"""Restore consumed approvals in isolated PostgreSQL databases; never dispatch mail."""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg

from personal_assistant.bootstrap import build_container
from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.core.approvals.service import ApprovalBinding, ApprovalStateError
from personal_assistant.core.jobs.outbox import SideEffectIntent, SideEffectState
from personal_assistant.core.mail.ports import MailDeliveryRecord, MailDeliveryStatus
from personal_assistant.domain.enums import ApprovalState
from personal_assistant.domain.models import utc_now
from personal_assistant.infrastructure.operations.recovery import export_database, restore_database
from personal_assistant.settings import Settings

BASE = os.getenv("PA_TEST_DATABASE_URL")


def database_url(value: str, name: str) -> str:
    parts = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://"))
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, "", ""))


@unittest.skipUnless(BASE, "set PA_TEST_DATABASE_URL for local temporary PostgreSQL")
class RecoveryApprovalPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not BASE or not urlsplit(BASE).path.endswith("_test"):
            raise RuntimeError("dedicated *_test base required")
        self.base = BASE
        self.names = ["pa_recovery_" + uuid4().hex + "_test" for _ in range(2)]
        self.admin_url = database_url(self.base, "postgres")
        admin = await asyncpg.connect(self.admin_url)
        try:
            for name in self.names:
                await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()
        self.tmp = tempfile.TemporaryDirectory(prefix="pa_recovery_")
        with patch.dict(os.environ, {}, clear=True):
            base = Settings.from_env()
        self.containers = []
        for index, name in enumerate(self.names):
            root = Path(self.tmp.name) / str(index)
            container = build_container(
                replace(
                    base,
                    environment="test",
                    storage_backend="postgres",
                    database_url=database_url(self.base, name),
                    extension_root=root / "extensions",
                    artifact_root=root / "artifacts",
                )
            )
            self.containers.append(container)
            await container.storage.startup()

    async def asyncTearDown(self):
        for container in self.containers:
            await container.aclose()
            await container.storage.close()
        admin = await asyncpg.connect(self.admin_url)
        try:
            for name in self.names:
                await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        finally:
            await admin.close()
        self.tmp.cleanup()

    async def test_restore_consumed_approval_matches_unknown_ledgers_and_cannot_execute(self):
        source, target = self.containers
        task = await source.tasks.create(objective="restore-only fixture", idempotency_key="task")
        binding = ApprovalBinding(
            action_type="mail.send",
            task_id=task.id,
            tool_id="fixture.send",
            tool_version="1",
            extension_id="fixture.mail",
            extension_version="1",
            target={"account_id": "fixture"},
            payload={"local_action_id": "restore-action"},
        )
        waiting = await source.approvals.prepare(binding)
        await source.approvals.approve(waiting.id, nonce=waiting.nonce, actor_id="reviewer")
        intent = SideEffectIntent(
            id="restore-intent",
            task_id=task.id,
            tool_id=binding.tool_id,
            idempotency_key="original-action",
            approval_id=waiting.id,
            canonical_payload_sha256=canonical_sha256(binding.payload),
            state=SideEffectState.EXECUTING,
            created_at=utc_now(),
            action_fingerprint=waiting.action_fingerprint,
        )
        await source.side_effect_outbox.create_with_approval_consumption(intent)
        executing = await source.approvals.get(waiting.id)
        self.assertEqual(ApprovalState.EXECUTING, executing.state)
        mail = MailDeliveryRecord(
            local_action_id="restore-action",
            account_id="fixture",
            message_id="<restore@example.test>",
            status=MailDeliveryStatus.PREPARED,
            envelope_digest="1" * 64,
            mime_sha256="2" * 64,
        )
        self.assertEqual(MailDeliveryStatus.PREPARED, await source.mail_ledger.prepare(mail))
        self.assertEqual(
            MailDeliveryStatus.EXECUTING,
            await source.mail_ledger.begin_execution(
                mail.local_action_id,
                mail.envelope_digest,
                owner_id="original-host",
                lease_seconds=60,
            ),
        )
        snapshot = await export_database(source.storage.database, source.settings.artifact_root)
        await restore_database(snapshot, target.storage.database)
        restored = await target.approvals.get(waiting.id)
        self.assertEqual(ApprovalState.UNKNOWN, restored.state)
        self.assertEqual(executing.approved_by, restored.approved_by)
        self.assertEqual(executing.approved_at, restored.approved_at)
        self.assertEqual(executing.consumed_at, restored.consumed_at)
        self.assertEqual(executing.canonical_action, restored.canonical_action)
        self.assertEqual(executing.action_fingerprint, restored.action_fingerprint)
        self.assertNotEqual(waiting.nonce, restored.nonce)
        self.assertEqual(executing.version + 1, restored.version)
        async with target.storage.database.connection() as connection:
            self.assertEqual(
                "UNKNOWN",
                await connection.fetchval(
                    "SELECT state FROM side_effect_intents WHERE id=$1",
                    intent.id,
                ),
            )
        restored_mail = await target.mail_ledger.get(mail.local_action_id)
        self.assertIsNotNone(restored_mail)
        self.assertEqual(MailDeliveryStatus.UNKNOWN, restored_mail.status)
        with self.assertRaises(ApprovalStateError):
            await target.approvals.approve(waiting.id, nonce=waiting.nonce, actor_id="reviewer")
        with self.assertRaises(ApprovalStateError):
            await target.approvals.consume_for_execution(waiting.id, binding)
        with self.assertRaises(ApprovalStateError):
            await target.side_effect_outbox.create_with_approval_consumption(
                replace(
                    intent,
                    id="forbidden-retry",
                    idempotency_key="different-key",
                )
            )
        self.assertEqual(MailDeliveryStatus.UNKNOWN, await target.mail_ledger.prepare(mail))
        self.assertEqual(
            MailDeliveryStatus.UNKNOWN,
            await target.mail_ledger.begin_execution(
                mail.local_action_id,
                mail.envelope_digest,
                owner_id="restored-host",
                lease_seconds=60,
            ),
        )
        async with target.storage.database.connection() as connection:
            self.assertEqual(
                1, await connection.fetchval("SELECT count(*) FROM side_effect_intents")
            )
            self.assertEqual(
                1, await connection.fetchval("SELECT count(*) FROM mail_delivery_actions")
            )
        self.assertEqual(ApprovalState.EXECUTING, (await source.approvals.get(waiting.id)).state)
