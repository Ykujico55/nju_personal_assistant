"""Local temporary PostgreSQL acceptance. Never reads or migrates the base database."""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg

from personal_assistant.bootstrap import build_container
from personal_assistant.domain import ConcurrentModificationError, TaskState
from personal_assistant.settings import Settings

BASE = os.getenv("PA_TEST_DATABASE_URL")


def with_db(value, name):
    parts = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://"))
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, "", ""))


@unittest.skipUnless(BASE, "set PA_TEST_DATABASE_URL for local temporary PostgreSQL")
class PostgresDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not urlsplit(BASE).path.endswith("_test"):
            raise RuntimeError("dedicated *_test base required")
        self.names = ["pa_delivery_" + uuid4().hex for _ in range(2)]
        self.admin_url = with_db(BASE, "postgres")
        admin = await asyncpg.connect(self.admin_url)
        try:
            for name in self.names:
                await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()
        self.tmp = tempfile.TemporaryDirectory(prefix="pa_delivery_")
        with patch.dict(os.environ, {}, clear=True):
            base = Settings.from_env()
        self.containers = []
        for index, name in enumerate(self.names):
            root = Path(self.tmp.name) / str(index)
            settings = replace(
                base,
                environment="test",
                storage_backend="postgres",
                database_url=with_db(BASE, name),
                extension_root=root / "extensions",
                artifact_root=root / "artifacts",
            )
            container = build_container(settings)
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

    async def test_records_reconnect_fingerprint_cas_and_transaction_rollback(self):
        from personal_assistant.infrastructure.database.records import PostgresRecordStore

        c = self.containers[0]
        await c.records.put("fixture", "r", {"value": 1}, expected_version=0)
        receipt = await c.records.command("key", {"value": 1}, AsyncMock(return_value={"ok": True}))
        reopened = PostgresRecordStore(c.storage.database, owner_id="owner")
        self.assertEqual(
            receipt,
            await reopened.command("key", {"value": 1}, AsyncMock(side_effect=AssertionError())),
        )
        with self.assertRaises(ConcurrentModificationError):
            await reopened.command("key", {"value": True}, AsyncMock())

        async def fail():
            await reopened.put("fixture", "r", {"value": 2}, expected_version=1)
            raise RuntimeError("rollback")

        with self.assertRaises(RuntimeError):
            await reopened.command("bad", {}, fail)
        self.assertEqual(1, (await reopened.get("fixture", "r"))["value"]["value"])
        with self.assertRaises(ConcurrentModificationError):
            await reopened.put("fixture", "r", {}, expected_version=0)

    async def test_durable_task_waits_without_model_and_restore_never_replays_jobs(self):
        from personal_assistant.infrastructure.operations.recovery import (
            export_database,
            restore_database,
        )
        from personal_assistant.runtime import build_runtime

        source, target = self.containers
        task = await source.tasks.create(objective="temporary local fixture", idempotency_key="c")
        await build_runtime(source).work_once()
        self.assertEqual(TaskState.WAITING_USER, (await source.tasks.get(task.id)).state)
        await source.jobs.enqueue(
            kind="agent.resume", payload={"task_id": task.id}, idempotency_key="pending"
        )
        snapshot = await export_database(source.storage.database, source.settings.artifact_root)
        await restore_database(snapshot, target.storage.database)
        self.assertEqual(TaskState.PAUSED_SAFETY, (await target.tasks.get(task.id)).state)
        self.assertIsNone(await target.jobs.claim(worker_id="restored"))
        with self.assertRaises(ValueError):
            await restore_database(snapshot, target.storage.database)
