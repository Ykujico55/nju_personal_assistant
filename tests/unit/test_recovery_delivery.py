from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from personal_assistant.infrastructure.memory.secrets import InMemorySecretStore


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemorySecretStore()
        self.handle = await self.store.put(name="fixture", kind="backup_key", value="01" * 32)

    async def test_encrypted_package_roundtrip_and_corruption_fails(self):
        from personal_assistant.infrastructure.operations.recovery import RecoveryVault

        vault = RecoveryVault(self.store, self.handle)
        snapshot = {
            "format": 1,
            "tables": {
                "tasks": [{"id": "t", "objective": "sensitive fixture", "status": "RUNNING"}]
            },
            "artifacts": {},
        }
        data = await vault.seal(snapshot)
        self.assertNotIn(b"sensitive fixture", data)
        self.assertEqual(snapshot, await vault.open(data))
        changed = bytearray(data)
        changed[-1] ^= 1
        with self.assertRaises(ValueError):
            await vault.open(bytes(changed))
        second = await self.store.put(name="other", kind="backup_key", value="02" * 32)
        with self.assertRaises(ValueError):
            await RecoveryVault(self.store, second).open(data)

    async def test_credentials_and_traversal_rejected_before_writes(self):
        from personal_assistant.infrastructure.operations.recovery import (
            RecoveryVault,
            stage_artifacts,
        )

        vault = RecoveryVault(self.store, self.handle)
        for table in ["browser_sessions", "push_subscriptions", "model_disclosure_consents"]:
            with self.assertRaises(ValueError):
                await vault.seal({"format": 1, "tables": {table: []}, "artifacts": {}})
        with self.assertRaises(ValueError):
            await vault.seal(
                {
                    "format": 1,
                    "tables": {"tasks": [{"password": "synthetic-secret"}]},
                    "artifacts": {},
                }
            )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "new"
            with self.assertRaises(ValueError):
                stage_artifacts({"../outside": {"sha256": "00" * 32, "data": ""}}, target)
            self.assertFalse(target.exists())

    async def test_restore_freezes_work_and_invalidates_approvals(self):
        from personal_assistant.infrastructure.operations.recovery import freeze_snapshot

        snapshot = {
            "format": 1,
            "tables": {
                "tasks": [{"status": "RUNNING"}],
                "agent_runs": [{"state": "RUNNING"}],
                "jobs": [{"state": "READY", "lease_owner": "w"}],
                "approvals": [{"status": "APPROVED", "nonce": "old"}],
                "side_effect_intents": [{"state": "EXECUTING"}],
                "mail_delivery_actions": [{"status": "EXECUTING"}],
                "runtime_records": [{"bucket": "schedules", "value": {"enabled": True}}],
            },
            "artifacts": {},
        }
        restored = freeze_snapshot(snapshot)
        self.assertEqual("PAUSED_SAFETY", restored["tables"]["tasks"][0]["status"])
        self.assertEqual("DEAD_LETTER", restored["tables"]["jobs"][0]["state"])
        self.assertEqual("EXPIRED", restored["tables"]["approvals"][0]["status"])
        self.assertNotEqual("old", restored["tables"]["approvals"][0]["nonce"])
        self.assertEqual("UNKNOWN", restored["tables"]["side_effect_intents"][0]["state"])
        self.assertEqual("UNKNOWN", restored["tables"]["mail_delivery_actions"][0]["status"])
        self.assertFalse(restored["tables"]["runtime_records"][0]["value"]["enabled"])
        self.assertEqual("RUNNING", snapshot["tables"]["tasks"][0]["status"])

    async def test_retention_keeps_latest_daily_weekly_monthly_points(self):
        from datetime import UTC, datetime, timedelta

        from personal_assistant.infrastructure.operations.recovery import retained_points

        now = datetime(2026, 10, 13, tzinfo=UTC)
        points = {f"p{n}": now - timedelta(hours=6 * n) for n in range(1000)}
        retained = retained_points(points)
        self.assertIn("p0", retained)
        self.assertLessEqual(len(retained), 17)
        self.assertGreaterEqual(len(retained), 7)

    async def test_periodic_backup_is_due_once_and_offsite_copy_is_encrypted(self):
        from datetime import UTC, datetime, timedelta
        from unittest.mock import AsyncMock

        from personal_assistant.infrastructure.operations.backups import BackupCoordinator
        from personal_assistant.infrastructure.operations.recovery import RecoveryVault

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = {
                "format": 1,
                "tables": {"tasks": [{"objective": "sensitive fixture"}]},
                "artifacts": {},
            }
            export = AsyncMock(return_value=snapshot)
            backups = BackupCoordinator(
                root / "managed",
                RecoveryVault(self.store, self.handle),
                export,
                offsite=root / "offsite",
            )
            now = datetime(2026, 10, 13, tzinfo=UTC)
            first = await backups.tick(now=now)
            self.assertTrue(first)
            self.assertIsNone(await backups.tick(now=now + timedelta(hours=1)))
            self.assertEqual(1, export.await_count)
            self.assertEqual(1, len(list((root / "offsite").glob("*.pa-backup"))))
            self.assertNotIn(
                b"sensitive fixture", next((root / "offsite").glob("*.pa-backup")).read_bytes()
            )
            self.assertIsNotNone(await backups.tick(now=now + timedelta(hours=30)))
            self.assertEqual(2, export.await_count)

    async def test_restore_preflight_rejects_database_aliases_and_nonempty_targets(self):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from personal_assistant.infrastructure.operations.recovery import preflight_restore_target

        target = AsyncMock()
        target.fetchval.return_value = "original_test"

        @asynccontextmanager
        async def connection(value):
            yield value

        target_db = SimpleNamespace(connection=lambda: connection(target))
        source_dsn = "postgresql://fixture@localhost/original_test"
        with self.assertRaises(ValueError):
            await preflight_restore_target(source_dsn, target_db)
        target.fetch.assert_not_awaited()
        target.fetch.return_value = [{"table_schema": "public", "table_name": "tasks"}]
        target.fetchval.side_effect = ["separate_restore", 1]
        with self.assertRaises(ValueError):
            await preflight_restore_target(source_dsn, target_db)
        target.fetchval.side_effect = ["separate_restore", 0]
        await preflight_restore_target(source_dsn, target_db)

    async def test_restore_execution_preserves_unknown_audit_and_burns_old_authority(self):
        from dataclasses import asdict, replace

        from personal_assistant.core.approvals.service import (
            ApprovalBinding,
            ApprovalService,
            ApprovalStateError,
            InMemoryApprovalRepository,
        )
        from personal_assistant.domain.enums import ApprovalState
        from personal_assistant.infrastructure.operations.recovery import freeze_snapshot

        binding = ApprovalBinding(
            action_type="mail.send",
            task_id="restore-task",
            tool_id="fixture.send",
            tool_version="1",
            extension_id="fixture.mail",
            extension_version="1",
            target={"account_id": "fixture"},
            payload={"local_action_id": "restore-action"},
        )
        source = ApprovalService(InMemoryApprovalRepository())
        waiting = await source.prepare(binding)
        approved = await source.approve(waiting.id, nonce=waiting.nonce, actor_id="reviewer")
        executing = await source.consume_for_execution(approved.id, binding)
        unknown = await source.mark_unknown(executing.id, reference_id="restore-action")
        for original in (executing, unknown):
            with self.subTest(state=original.state):
                row = asdict(original)
                row["status"] = row.pop("state").value
                snapshot = {
                    "format": 1,
                    "tables": {
                        "approvals": [row],
                        "side_effect_intents": [{"state": original.state.value}],
                        "mail_delivery_actions": [{"status": original.state.value}],
                    },
                    "artifacts": {},
                }
                restored = freeze_snapshot(snapshot)["tables"]
                approval = restored["approvals"][0]
                self.assertEqual("UNKNOWN", approval["status"])
                self.assertEqual("UNKNOWN", restored["side_effect_intents"][0]["state"])
                self.assertEqual("UNKNOWN", restored["mail_delivery_actions"][0]["status"])
                for field in (
                    "approved_by",
                    "approved_at",
                    "consumed_at",
                    "completed_at",
                    "canonical_action",
                    "action_fingerprint",
                    "result_reference",
                ):
                    self.assertEqual(row[field], approval[field], field)
                if original.state is ApprovalState.EXECUTING:
                    self.assertNotEqual(original.nonce, approval["nonce"])
                    self.assertEqual(original.version + 1, approval["version"])
                repository = InMemoryApprovalRepository()
                await repository.create(
                    replace(
                        original,
                        state=ApprovalState(approval["status"]),
                        nonce=approval["nonce"],
                        version=approval["version"],
                        approved_by=approval["approved_by"],
                        approved_at=approval["approved_at"],
                        consumed_at=approval["consumed_at"],
                    )
                )
                service = ApprovalService(repository)
                with self.assertRaises(ApprovalStateError):
                    await service.approve(original.id, nonce=waiting.nonce, actor_id="reviewer")
                with self.assertRaises(ApprovalStateError):
                    await service.consume_for_execution(original.id, binding)
                self.assertEqual(original.state.value, snapshot["tables"]["approvals"][0]["status"])
