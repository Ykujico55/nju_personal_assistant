"""Real PostgreSQL acceptance tests for the F01 persistence kernel.

These tests only run when ``PA_TEST_DATABASE_URL`` points at a temporary test
database whose name ends in ``_test``. They create and drop their own uniquely
named throwaway databases; they never touch the base database contents.

Run with::

    $env:PA_TEST_DATABASE_URL = "postgresql://assistant:change-me@127.0.0.1:5432/assistant_test"
    .venv-win\\Scripts\\python.exe -m pytest tests/integration/test_postgres_f01.py -v
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg

from personal_assistant.core.agent.checkpoint import Observation
from personal_assistant.core.agent.engine import (
    AgentEngine,
    DecisionKind,
    ValidatedDecision,
)
from personal_assistant.core.approvals import (
    ApprovalBinding,
    ApprovalBindingMismatchError,
    ApprovalExpiredError,
    ApprovalService,
    ApprovalStateError,
    InMemoryApprovalRepository,
)
from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.core.context import ContextManager
from personal_assistant.core.extensions import ExtensionRecord, ExtensionState
from personal_assistant.core.extensions.manifest import (
    DeclaredCapabilities,
    ExtensionManifest,
    ManifestTool,
)
from personal_assistant.core.jobs import (
    JobState,
    LeaseConflict,
    SideEffectIntent,
    SideEffectState,
)
from personal_assistant.core.tasks import TaskService
from personal_assistant.core.tools import ToolGateway, ToolPolicy, ToolRegistry
from personal_assistant.domain import (
    ConcurrentModificationError,
    RiskLevel,
    TaskRun,
    TaskState,
    ToolCall,
    ToolDescriptor,
    ValidationError,
    utc_now,
)
from personal_assistant.infrastructure.database import (
    MigrationChecksumError,
    MigrationError,
    PostgresAdapterConfig,
    PostgresAdapters,
    build_postgres_adapters,
)

BASE_URL = os.getenv("PA_TEST_DATABASE_URL")
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres://", "postgresql://"
    )


def _with_db(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, "", ""))


class _FailingCheckpointStore:
    """Wraps the real store and fails after the run CAS to prove rollback."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def load(self, run_id: str) -> dict[str, Any]:
        return await self._inner.load(run_id)  # type: ignore[no-any-return]

    async def save(self, run: TaskRun, snapshot: dict[str, Any]) -> None:
        del run, snapshot
        raise RuntimeError("injected checkpoint failure")


class _Planner:
    def __init__(self) -> None:
        self.calls = 0

    async def propose(self, **kwargs: Any) -> str:
        del kwargs
        self.calls += 1
        return "call" if self.calls == 1 else "complete"


class _Validator:
    async def validate(self, proposal: Any, run: TaskRun) -> ValidatedDecision:
        if proposal == "complete":
            return ValidatedDecision(DecisionKind.COMPLETE, result={"done": True})
        return ValidatedDecision(
            DecisionKind.CALL_TOOL,
            tool_call=ToolCall(
                tool_id="example.read",
                tool_version="1",
                arguments={},
                task_id=run.task_id,
                workflow_allowed_tools=frozenset({"example.read"}),
            ),
        )


class _Executor:
    async def execute(self, descriptor: Any, arguments: Any, context: Any) -> Any:
        del descriptor, arguments, context
        return {"value": "evidence"}


@unittest.skipUnless(BASE_URL, "set PA_TEST_DATABASE_URL to run real PostgreSQL tests")
class PostgresF01Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = _dsn(BASE_URL or "")
        base_name = urlsplit(base).path.lstrip("/")
        if not base_name.endswith("_test"):
            raise RuntimeError(
                "PA_TEST_DATABASE_URL must name a dedicated *_test database"
            )
        self._base = base
        self._admin_dsn = _with_db(base, "postgres")
        self._db_name = f"pa_f01_test_{uuid4().hex}"
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{self._db_name}"')
        finally:
            await admin.close()
        self.url = _with_db(base, self._db_name)
        self._temp_dirs: list[str] = []
        self._adapters: list[PostgresAdapters] = []

    async def asyncTearDown(self) -> None:
        for adapters in reversed(self._adapters):
            await adapters.close()
        for directory in self._temp_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        # Only ever drop the database this test created.
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                self._db_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{self._db_name}"')
        finally:
            await admin.close()

    def adapters(self, migrations_dir: Path | None = None) -> PostgresAdapters:
        adapters = build_postgres_adapters(
            PostgresAdapterConfig(database_url=self.url, migrations_dir=migrations_dir)
        )
        self._adapters.append(adapters)
        return adapters

    async def migrate(self, migrations_dir: Path | None = None) -> PostgresAdapters:
        adapters = self.adapters(migrations_dir)
        await adapters.startup()
        return adapters

    def task_service(self, adapters: PostgresAdapters) -> TaskService:
        return TaskService(
            repository=adapters.task_repository,
            queue=adapters.job_queue,
            audit=adapters.audit_writer,
            events=adapters.event_stream,
            unit_of_work=adapters.database,
        )

    def _temp_migrations(self, *, drift_0001: bool = False, only_0001: bool = False) -> Path:
        directory = tempfile.mkdtemp(prefix="pa_f01_migrations_")
        self._temp_dirs.append(directory)
        target = Path(directory)
        source_0001 = (MIGRATIONS_DIR / "0001_core.sql").read_bytes()
        if drift_0001:
            source_0001 += b"\n-- checksum drift fixture\n"
        (target / "0001_core.sql").write_bytes(source_0001)
        if not only_0001:
            shutil.copy(MIGRATIONS_DIR / "0002_f01_persistence.sql", target)
        return target

    async def _fetchval(self, adapters: PostgresAdapters, query: str, *args: Any) -> Any:
        async with adapters.database.connection() as connection:
            return await connection.fetchval(query, *args)

    async def _fetch(self, adapters: PostgresAdapters, query: str, *args: Any) -> list[Any]:
        async with adapters.database.connection() as connection:
            return list(await connection.fetch(query, *args))

    # -- migration contract -------------------------------------------------
    async def test_empty_database_migrates_and_reruns_are_noops(self) -> None:
        adapters = self.adapters()
        applied = await adapters.startup()
        self.assertEqual(("0001_core", "0002_f01_persistence"), applied)
        rows = await self._fetch(
            adapters, "SELECT version, checksum FROM schema_migrations ORDER BY version"
        )
        self.assertEqual(["0001_core", "0002_f01_persistence"], [r["version"] for r in rows])
        for row in rows:
            self.assertEqual(64, len(row["checksum"]))
        # Repeat startup must be a no-op, not a re-application.
        self.assertEqual((), await adapters.startup())

    async def test_database_with_only_0001_upgrades(self) -> None:
        legacy_dir = self._temp_migrations(only_0001=True)
        first = self.adapters(legacy_dir)
        self.assertEqual(("0001_core",), await first.startup())
        # Same database, now seen by the real (full) migration directory.
        second = self.adapters()
        self.assertEqual(("0002_f01_persistence",), await second.startup())
        versions = await self._fetchval(
            second, "SELECT count(*) FROM schema_migrations"
        )
        self.assertEqual(2, versions)

    async def test_checksum_drift_is_rejected(self) -> None:
        await self.migrate()
        drifting = self._temp_migrations(drift_0001=True)
        late = self.adapters(drifting)
        with self.assertRaises(MigrationChecksumError):
            await late.startup()
        await late.close()
        self._adapters.remove(late)

    async def test_missing_migrations_directory_fails_closed(self) -> None:
        empty = Path(tempfile.mkdtemp(prefix="pa_f01_empty_"))
        self._temp_dirs.append(str(empty))
        adapters = self.adapters(empty)
        with self.assertRaises(MigrationError):
            await adapters.startup()

    # -- restart / recovery -------------------------------------------------
    async def test_all_state_survives_reconnect(self) -> None:
        adapters = await self.migrate()
        service = self.task_service(adapters)
        task = await service.create(objective="recover me", idempotency_key="create-1")
        detail = await service.get(task.id)
        await service.add_message(
            task_id=task.id,
            content="context",
            expected_version=detail.version,
            actor="owner",
            idempotency_key="msg-1",
        )
        run = TaskRun(id=f"run_{uuid4().hex}", task_id=task.id, objective="recover me")
        await adapters.run_repository.add(run)
        await adapters.checkpoint_store.save(run, {"last": "checkpoint"})
        await adapters.observation_store.append(Observation(run.id, {"observed": True}))
        approval_service = ApprovalService(adapters.approval_repository)
        approval = await approval_service.prepare(self._binding(task.id))
        await approval_service.approve(approval.id, nonce=approval.nonce, actor_id="owner")
        job = await adapters.job_queue.enqueue(
            kind="agent.start", payload={"task_id": task.id}, idempotency_key="job-1"
        )
        await adapters.audit_writer.append(
            self._audit_event("task.created", task.id, {"state": "QUEUED"})
        )
        await adapters.lifecycle_store.save(self._extension_record())

        await adapters.close()
        rebuilt = await self.migrate()

        recovered_task = await rebuilt.task_repository.get(task.id)
        self.assertEqual(TaskState.QUEUED, recovered_task.state)
        self.assertEqual(1, len(await rebuilt.task_repository.messages(task.id)))
        events = await rebuilt.event_stream.after(0)
        self.assertTrue(any(event.type == "task.queued" for event in events))
        self.assertEqual("recover me", (await rebuilt.run_repository.get(run.id)).objective)
        self.assertEqual(
            {"last": "checkpoint", "run_version": 0},
            await rebuilt.checkpoint_store.load(run.id),
        )
        self.assertEqual(
            JobState.READY, (await rebuilt.job_queue.get(job.id)).state  # type: ignore[union-attr]
        )
        recovered_approval = await rebuilt.approval_repository.get(approval.id)
        self.assertEqual("APPROVED", recovered_approval.state.value)
        self.assertGreaterEqual(
            await self._fetchval(rebuilt, "SELECT count(*) FROM audit_events"), 1
        )
        recovered_extension = await rebuilt.lifecycle_store.get("demo.weather")
        self.assertIsNotNone(recovered_extension)
        assert recovered_extension is not None
        self.assertEqual(ExtensionState.ENABLED, recovered_extension.state)
        self.assertEqual("demo.weather.get", recovered_extension.manifest.tools[0].id)

    # -- queue lease semantics ---------------------------------------------
    async def test_concurrent_claim_has_single_owner(self) -> None:
        first = await self.migrate()
        second = self.adapters()
        await second.startup()
        job = await first.job_queue.enqueue(
            kind="agent.start", payload={"n": 1}, idempotency_key="claim-1"
        )
        results = await asyncio.gather(
            first.job_queue.claim(worker_id="worker-a", lease_seconds=60),
            second.job_queue.claim(worker_id="worker-b", lease_seconds=60),
        )
        owners = [result for result in results if result is not None]
        self.assertEqual(1, len(owners))
        self.assertEqual(job.id, owners[0].id)
        leases = await self._fetch(
            first,
            "SELECT count(*) AS n FROM jobs WHERE id = $1 AND state = 'LEASED' "
            "AND lease_owner IS NOT NULL AND lease_until > now()",
            job.id,
        )
        self.assertEqual(1, leases[0]["n"])

    async def test_expired_lease_is_reclaimed_and_old_owner_rejected(self) -> None:
        adapters = await self.migrate()
        job = await adapters.job_queue.enqueue(
            kind="agent.start", payload={"n": 1}, idempotency_key="lease-1"
        )
        claimed = await adapters.job_queue.claim(worker_id="worker-a", lease_seconds=60)
        assert claimed is not None
        async with adapters.database.connection() as connection:
            await connection.execute(
                "UPDATE jobs SET lease_until = now() - interval '1 second' WHERE id = $1",
                job.id,
            )
        with self.assertRaises(LeaseConflict):
            await adapters.job_queue.complete(
                job_id=job.id, worker_id="worker-a", result={}
            )
        reclaimed = await adapters.job_queue.claim(worker_id="worker-b", lease_seconds=60)
        assert reclaimed is not None
        self.assertEqual("worker-b", reclaimed.lease_owner)
        with self.assertRaises(LeaseConflict):
            await adapters.job_queue.heartbeat(job_id=job.id, worker_id="worker-a")
        finished = await adapters.job_queue.complete(
            job_id=job.id, worker_id="worker-b", result={"ok": True}
        )
        self.assertEqual(JobState.SUCCEEDED, finished.state)

    async def test_unknown_job_is_never_reclaimed_after_restart(self) -> None:
        adapters = await self.migrate()
        job = await adapters.job_queue.enqueue(
            kind="mail.send", payload={"n": 1}, idempotency_key="unknown-1"
        )
        claimed = await adapters.job_queue.claim(worker_id="worker-a", lease_seconds=60)
        assert claimed is not None
        await adapters.job_queue.mark_unknown(
            job_id=job.id, worker_id="worker-a", diagnostic_code="SMTP_UNKNOWN"
        )
        await adapters.close()
        rebuilt = await self.migrate()
        self.assertIsNone(await rebuilt.job_queue.claim(worker_id="worker-b", lease_seconds=60))
        stored = await rebuilt.job_queue.get(job.id)
        assert stored is not None
        self.assertEqual(JobState.WAITING_RECONCILIATION, stored.state)

    # -- optimistic concurrency --------------------------------------------
    async def test_stale_task_run_and_approval_writes_fail(self) -> None:
        adapters = await self.migrate()
        service = self.task_service(adapters)
        task = await service.create(objective="cas", idempotency_key="cas-1")
        current = await service.get(task.id)
        from dataclasses import replace

        bumped = replace(current, state=TaskState.RUNNING)
        await adapters.task_repository.save(bumped, expected_version=current.version)
        stale = replace(current, state=TaskState.FAILED)
        with self.assertRaises(ConcurrentModificationError):
            await adapters.task_repository.save(stale, expected_version=current.version)
        self.assertEqual(
            TaskState.RUNNING,
            (await adapters.task_repository.get(task.id)).state,
        )

        run = TaskRun(id=f"run_{uuid4().hex}", task_id=task.id, objective="cas")
        await adapters.run_repository.add(run)
        await adapters.run_repository.save(
            replace(run, no_progress_rounds=1), expected_version=0
        )
        with self.assertRaises(ConcurrentModificationError):
            await adapters.run_repository.save(
                replace(run, no_progress_rounds=99), expected_version=0
            )
        self.assertEqual(1, (await adapters.run_repository.get(run.id)).no_progress_rounds)

        approval_service = ApprovalService(adapters.approval_repository)
        approval = await approval_service.prepare(self._binding(task.id))
        with self.assertRaises(ConcurrentModificationError):
            await adapters.approval_repository.save(approval, expected_version=5)

    # -- idempotency --------------------------------------------------------
    async def test_idempotent_replay_and_payload_conflict(self) -> None:
        adapters = await self.migrate()
        service = self.task_service(adapters)
        task = await service.create(objective="same", idempotency_key="dup")
        replay = await service.create(objective="same", idempotency_key="dup")
        self.assertEqual(task.id, replay.id)
        with self.assertRaises(ConcurrentModificationError):
            await service.create(objective="different", idempotency_key="dup")

        current = await service.get(task.id)
        _, message = await service.add_message(
            task_id=task.id,
            content="hello",
            expected_version=current.version,
            actor="owner",
            idempotency_key="mdup",
        )
        _, replay_message = await service.add_message(
            task_id=task.id,
            content="hello",
            expected_version=current.version,
            actor="owner",
            idempotency_key="mdup",
        )
        self.assertEqual(message.id, replay_message.id)
        with self.assertRaises(ConcurrentModificationError):
            await service.add_message(
                task_id=task.id,
                content="changed",
                expected_version=current.version,
                actor="owner",
                idempotency_key="mdup",
            )

        job = await adapters.job_queue.enqueue(
            kind="agent.start", payload={"n": 1}, idempotency_key="jdup"
        )
        again = await adapters.job_queue.enqueue(
            kind="agent.start", payload={"n": 1}, idempotency_key="jdup"
        )
        self.assertEqual(job.id, again.id)
        with self.assertRaises(ValueError):
            await adapters.job_queue.enqueue(
                kind="agent.start", payload={"n": 2}, idempotency_key="jdup"
            )

        cancel = await service.cancel(
            task_id=task.id,
            expected_version=(await service.get(task.id)).version,
            actor="owner",
            idempotency_key="cdup",
        )
        cancel_replay = await service.cancel(
            task_id=task.id,
            expected_version=999,
            actor="owner",
            idempotency_key="cdup",
        )
        self.assertEqual(cancel.state, cancel_replay.state)

    # -- approval + outbox atomicity ---------------------------------------
    async def test_approval_consumption_and_outbox_are_atomic(self) -> None:
        adapters = await self.migrate()
        service = self.task_service(adapters)
        task = await service.create(objective="outbox", idempotency_key="ob-1")
        approval_service = ApprovalService(adapters.approval_repository)

        binding = self._binding(task.id)
        approval = await approval_service.prepare(binding)
        await approval_service.approve(approval.id, nonce=approval.nonce, actor_id="owner")
        intent = self._intent(task.id, approval.id, binding, key="send-1")
        await adapters.side_effect_outbox.create_with_approval_consumption(intent)
        self.assertEqual(
            "EXECUTING", (await approval_service.get(approval.id)).state.value
        )
        self.assertEqual(
            1, await self._fetchval(adapters, "SELECT count(*) FROM side_effect_intents")
        )

        # Duplicate (tool_id, idempotency_key) must roll back the whole unit.
        second = await approval_service.prepare(binding)
        await approval_service.approve(second.id, nonce=second.nonce, actor_id="owner")
        duplicate = self._intent(task.id, second.id, binding, key="send-1")
        with self.assertRaises(ValidationError):
            await adapters.side_effect_outbox.create_with_approval_consumption(duplicate)
        self.assertEqual("APPROVED", (await approval_service.get(second.id)).state.value)
        self.assertEqual(
            1, await self._fetchval(adapters, "SELECT count(*) FROM side_effect_intents")
        )

        # Non-approved approval must insert no intent.
        waiting = await approval_service.prepare(binding)
        bad = self._intent(task.id, waiting.id, binding, key="send-2")
        with self.assertRaises(ApprovalStateError):
            await adapters.side_effect_outbox.create_with_approval_consumption(bad)
        self.assertEqual(
            1, await self._fetchval(adapters, "SELECT count(*) FROM side_effect_intents")
        )
        self.assertEqual("WAITING_APPROVAL", (await approval_service.get(waiting.id)).state.value)

    async def test_approval_unknown_survives_restart(self) -> None:
        adapters = await self.migrate()
        service = self.task_service(adapters)
        task = await service.create(objective="unknown-approval", idempotency_key="ua-1")
        approval_service = ApprovalService(adapters.approval_repository)
        binding = self._binding(task.id)
        approval = await approval_service.prepare(binding)
        await approval_service.approve(approval.id, nonce=approval.nonce, actor_id="owner")
        await approval_service.consume_for_execution(approval.id, binding)
        await approval_service.mark_unknown(approval.id, reference_id="unknown:1")
        await adapters.close()
        rebuilt = await self.migrate()
        recovered = await ApprovalService(rebuilt.approval_repository).get(approval.id)
        self.assertEqual("UNKNOWN", recovered.state.value)

    # -- audit redaction ----------------------------------------------------
    async def test_audit_is_redacted_before_storage(self) -> None:
        adapters = await self.migrate()
        secret = "fixture-password-please-redact"
        token = "fixture-token-please-redact"
        await adapters.audit_writer.append(
            self._audit_event(
                "auth.attempt",
                "res-1",
                {"password": secret, "nested": {"token": token}, "safe": "visible"},
            )
        )
        rows = await self._fetch(
            adapters,
            "SELECT event_type, actor, resource_type, resource_id, data::text AS data "
            "FROM audit_events",
        )
        blob = " ".join(
            " ".join(str(value) for value in row.values()) for row in rows
        )
        self.assertNotIn(secret, blob)
        self.assertNotIn(token, blob)
        self.assertIn("[REDACTED]", blob)
        self.assertIn("visible", blob)

    # -- production wiring --------------------------------------------------
    async def test_build_postgres_adapters_exposes_full_port_set(self) -> None:
        adapters = self.adapters()
        for attribute in (
            "task_repository",
            "event_stream",
            "job_queue",
            "approval_repository",
            "run_repository",
            "checkpoint_store",
            "observation_store",
            "audit_writer",
            "side_effect_outbox",
            "lifecycle_store",
        ):
            self.assertTrue(hasattr(adapters, attribute), attribute)

    async def test_event_sequence_is_global_and_notify_wakes_readers(self) -> None:
        first = await self.migrate()
        second = self.adapters()
        await second.startup()
        e1 = await first.event_stream.publish("task.queued", "t1", {"a": 1})
        e2 = await second.event_stream.publish("task.queued", "t2", {"b": 2})
        self.assertLess(e1.sequence, e2.sequence)
        seen = await second.event_stream.after(0)
        self.assertEqual([e1.sequence, e2.sequence], [event.sequence for event in seen])

        pending = asyncio.create_task(
            first.event_stream.wait_after(e2.sequence, timeout_seconds=10.0)
        )
        await asyncio.sleep(0.5)
        e3 = await second.event_stream.publish("task.cancelled", "t1", {"c": 3})
        woke = await pending
        self.assertEqual([e3.sequence], [event.sequence for event in woke])

    async def test_production_container_starts_on_postgres_and_does_not_fall_back(
        self,
    ) -> None:
        from personal_assistant.bootstrap import build_container
        from personal_assistant.infrastructure.database.job_queue import PostgresJobQueue
        from personal_assistant.infrastructure.memory import InMemoryJobQueue

        settings = self._production_settings(self.url)
        container = build_container(settings)
        self.assertIsInstance(container.jobs, PostgresJobQueue)
        self.assertNotIsInstance(container.jobs, InMemoryJobQueue)
        await container.storage.startup()
        try:
            task = await container.tasks.create(objective="prod", idempotency_key="p1")
            self.assertEqual(TaskState.QUEUED, task.state)
        finally:
            await container.storage.close()

    async def test_production_container_fails_closed_when_database_unreachable(self) -> None:
        from personal_assistant.bootstrap import build_container

        unreachable = "postgresql://assistant:change-me@127.0.0.1:59999/assistant_test"
        settings = self._production_settings(unreachable)
        container = build_container(settings)
        with self.assertRaises((OSError, asyncpg.PostgresError)):
            await container.storage.startup()
        await container.storage.close()

    async def test_expired_approval_cannot_be_consumed(self) -> None:
        adapters = await self.migrate()
        service = self.task_service(adapters)
        task = await service.create(objective="expired", idempotency_key="exp-1")
        approval_service = ApprovalService(adapters.approval_repository)
        binding = self._binding(task.id)
        approval = await approval_service.prepare(binding)
        await approval_service.approve(approval.id, nonce=approval.nonce, actor_id="owner")
        async with adapters.database.connection() as connection:
            await connection.execute(
                "UPDATE approvals SET expires_at = now() - interval '5 minutes' WHERE id = $1",
                approval.id,
            )
        with self.assertRaises(ApprovalExpiredError):
            await adapters.side_effect_outbox.create_with_approval_consumption(
                self._intent(task.id, approval.id, binding, key="exp-send")
            )
        self.assertEqual("EXPIRED", (await approval_service.get(approval.id)).state.value)
        self.assertEqual(
            0, await self._fetchval(adapters, "SELECT count(*) FROM side_effect_intents")
        )

    async def test_missing_action_fingerprint_is_rejected(self) -> None:
        from dataclasses import replace

        adapters = await self.migrate()
        service = self.task_service(adapters)
        task = await service.create(objective="nofp", idempotency_key="nofp-1")
        approval_service = ApprovalService(adapters.approval_repository)
        binding = self._binding(task.id)
        approval = await approval_service.prepare(binding)
        await approval_service.approve(approval.id, nonce=approval.nonce, actor_id="owner")
        blank = replace(
            self._intent(task.id, approval.id, binding, key="nofp-send"),
            action_fingerprint="",
        )
        with self.assertRaises(ValidationError):
            await adapters.side_effect_outbox.create_with_approval_consumption(blank)
        self.assertEqual("APPROVED", (await approval_service.get(approval.id)).state.value)
        self.assertEqual(
            0, await self._fetchval(adapters, "SELECT count(*) FROM side_effect_intents")
        )

    async def test_drifted_binding_burns_approval_without_intent(self) -> None:
        adapters = await self.migrate()
        service = self.task_service(adapters)
        task = await service.create(objective="drift", idempotency_key="drift-1")
        approval_service = ApprovalService(adapters.approval_repository)
        binding = self._binding(task.id)
        approval = await approval_service.prepare(binding)
        await approval_service.approve(approval.id, nonce=approval.nonce, actor_id="owner")
        drifted = self._intent(
            task.id, approval.id, binding, key="drift-send", fingerprint="0" * 64
        )
        with self.assertRaises(ApprovalBindingMismatchError):
            await adapters.side_effect_outbox.create_with_approval_consumption(drifted)
        self.assertEqual(
            "CANCELLED", (await approval_service.get(approval.id)).state.value
        )
        self.assertEqual(
            0, await self._fetchval(adapters, "SELECT count(*) FROM side_effect_intents")
        )

    async def test_finalize_commits_approval_and_intent_together(self) -> None:
        adapters = await self.migrate()
        service = self.task_service(adapters)
        task = await service.create(objective="finalize", idempotency_key="fin-1")
        approval_service = ApprovalService(adapters.approval_repository)
        binding = self._binding(task.id)

        approval = await approval_service.prepare(binding)
        await approval_service.approve(approval.id, nonce=approval.nonce, actor_id="owner")
        intent = self._intent(task.id, approval.id, binding, key="fin-send")
        await adapters.side_effect_outbox.create_with_approval_consumption(intent)
        await adapters.side_effect_outbox.finalize(
            intent.id,
            approval.id,
            state=SideEffectState.SUCCEEDED,
            result_reference="receipt:1",
            receipt={"reference": "receipt:1"},
        )
        self.assertEqual("SUCCEEDED", (await approval_service.get(approval.id)).state.value)
        row = await self._fetch(
            adapters, "SELECT state FROM side_effect_intents WHERE id = $1", intent.id
        )
        self.assertEqual("SUCCEEDED", row[0]["state"])

        # A finalize against a missing intent must not advance the approval.
        second_binding = ApprovalBinding(
            action_type="mail.send",
            task_id=task.id,
            tool_id="smail.send",
            tool_version="1",
            extension_id="nju.smail",
            extension_version="0.1.0",
            target={"to": "other@example.edu"},
            payload={"subject": "second", "body": "body"},
        )
        second = await approval_service.prepare(second_binding)
        await approval_service.approve(second.id, nonce=second.nonce, actor_id="owner")
        second_intent = self._intent(
            task.id, second.id, second_binding, key="fin-send-2"
        )
        await adapters.side_effect_outbox.create_with_approval_consumption(second_intent)
        from personal_assistant.domain import NotFoundError

        with self.assertRaises(NotFoundError):
            await adapters.side_effect_outbox.finalize(
                "intent_missing",
                second.id,
                state=SideEffectState.UNKNOWN,
                result_reference="unknown:1",
            )
        self.assertEqual(
            "EXECUTING", (await approval_service.get(second.id)).state.value
        )

    async def test_engine_round_is_one_transaction(self) -> None:
        adapters = await self.migrate()
        service = self.task_service(adapters)
        task = await service.create(objective="engine", idempotency_key="eng-1")
        run = TaskRun(id=f"run_{uuid4().hex}", task_id=task.id, objective="engine")
        await adapters.run_repository.add(run)
        engine = self._engine(
            adapters, checkpoints=_FailingCheckpointStore(adapters.checkpoint_store)
        )
        with self.assertRaises(RuntimeError):
            await engine.run(run.id)
        # The run CAS must have rolled back with the failed checkpoint.
        self.assertEqual(0, (await adapters.run_repository.get(run.id)).version)
        self.assertEqual(
            0,
            await self._fetchval(
                adapters,
                "SELECT count(*) FROM run_observations WHERE run_id = $1",
                run.id,
            ),
        )

    async def test_engine_completes_and_persists_checkpoints(self) -> None:
        adapters = await self.migrate()
        service = self.task_service(adapters)
        task = await service.create(objective="engine-ok", idempotency_key="engo-1")
        run = TaskRun(id=f"run_{uuid4().hex}", task_id=task.id, objective="engine-ok")
        await adapters.run_repository.add(run)
        engine = self._engine(adapters, checkpoints=adapters.checkpoint_store)
        result = await engine.run(run.id)
        self.assertEqual(TaskState.SUCCEEDED, result.state)
        checkpoint = await adapters.checkpoint_store.load(run.id)
        self.assertIn("run_version", checkpoint)
        self.assertEqual(
            2,
            await self._fetchval(
                adapters,
                "SELECT count(*) FROM run_observations WHERE run_id = $1",
                run.id,
            ),
        )

    async def test_populated_0001_snapshot_upgrades(self) -> None:
        from personal_assistant.infrastructure.database.lifecycle_store import (
            _manifest_to_dict,
        )

        legacy_dir = self._temp_migrations(only_0001=True)
        legacy = self.adapters(legacy_dir)
        await legacy.startup()
        manifest = _manifest_to_dict(
            self._extension_record("demo.legacy").manifest
        )
        async with legacy.database.connection() as connection:
            await connection.execute(
                "INSERT INTO tasks (id, owner_id, objective, status, version) "
                "VALUES ($1, $2, $3, 'QUEUED', 0)",
                "task_legacy",
                "owner",
                "legacy objective",
            )
            await connection.execute(
                "INSERT INTO agent_runs "
                "(id, task_id, state, round_count, no_progress_rounds, "
                " consecutive_errors, started_at, updated_at, version) "
                "VALUES ($1, $2, 'RUNNING', 0, 0, 0, now(), now(), 0)",
                "run_legacy",
                "task_legacy",
            )
            await connection.execute(
                "INSERT INTO run_checkpoints (run_id, sequence, state, snapshot) "
                "VALUES ($1, 1, 'RUNNING', $2)",
                "run_legacy",
                {"step": 1},
            )
            await connection.execute(
                "INSERT INTO extensions "
                "(id, active_version, lifecycle_state, retained_data, registry_generation) "
                "VALUES ('demo.legacy', '0.1.0', 'ENABLED', true, 0)"
            )
            await connection.execute(
                "INSERT INTO extension_versions "
                "(extension_id, version, source_sha256, manifest, install_path) "
                "VALUES ($1, $2, $3, $4, $5)",
                "demo.legacy",
                "0.1.0",
                "a" * 64,
                manifest,
                "/opt/demo_legacy",
            )

        upgraded = self.adapters()
        await upgraded.startup()
        recovered_run = await upgraded.run_repository.get("run_legacy")
        self.assertEqual("legacy objective", recovered_run.objective)
        self.assertEqual({"step": 1}, await upgraded.checkpoint_store.load("run_legacy"))
        recovered_extension = await upgraded.lifecycle_store.get("demo.legacy")
        self.assertIsNotNone(recovered_extension)
        assert recovered_extension is not None
        self.assertEqual("demo.legacy", recovered_extension.manifest.id)
        # Package version is 0.1.0, manifest format version is 1.
        self.assertEqual("0.1.0", recovered_extension.manifest.version)
        self.assertEqual("1", recovered_extension.manifest.manifest_version)
        self.assertEqual("/opt/demo_legacy", recovered_extension.install_path)
        self.assertEqual("sha256:" + "a" * 64, recovered_extension.artifact_hash)

        # Appending to a legacy run must not restart the checkpoint sequence at 1.
        await upgraded.checkpoint_store.save(recovered_run, {"step": 2})
        self.assertEqual(
            {"step": 2, "run_version": recovered_run.version},
            await upgraded.checkpoint_store.load("run_legacy"),
        )
        sequences = await self._fetch(
            upgraded,
            "SELECT sequence FROM run_checkpoints WHERE run_id = $1 ORDER BY sequence",
            "run_legacy",
        )
        self.assertEqual([1, 2], [row["sequence"] for row in sequences])

    # -- fixtures -----------------------------------------------------------
    @staticmethod
    def _production_settings(database_url: str):
        from personal_assistant.settings import Settings

        return Settings(
            environment="production",
            log_level="INFO",
            public_host="127.0.0.1",
            public_port=8000,
            admin_host="127.0.0.1",
            admin_port=8001,
            health_host="127.0.0.1",
            health_port=8010,
            storage_backend="postgres",
            database_url=database_url,
            extension_root=Path("./var/extensions"),
            artifact_root=Path("./var/artifacts"),
            trust_cloudflare_access=True,
            public_origin="https://assistant.example.test",
            cf_access_team_domain="team.cloudflareaccess.com",
            cf_access_aud="audience",
        )
    @staticmethod
    def _binding(task_id: str) -> ApprovalBinding:
        return ApprovalBinding(
            action_type="mail.send",
            task_id=task_id,
            tool_id="smail.send",
            tool_version="1",
            extension_id="nju.smail",
            extension_version="0.1.0",
            target={"to": "student@example.edu"},
            payload={"subject": "hello", "body": "body"},
        )

    @staticmethod
    def _intent(
        task_id: str,
        approval_id: str,
        binding: ApprovalBinding,
        *,
        key: str,
        fingerprint: str | None = None,
    ) -> SideEffectIntent:
        return SideEffectIntent(
            id=f"intent_{uuid4().hex}",
            task_id=task_id,
            tool_id="smail.send",
            idempotency_key=key,
            approval_id=approval_id,
            canonical_payload_sha256=canonical_sha256(
                binding.envelope()["payload"]
            ),
            state=SideEffectState.PREPARED,
            created_at=utc_now(),
            action_fingerprint=(
                fingerprint
                if fingerprint is not None
                else canonical_sha256(binding.envelope())
            ),
        )

    @staticmethod
    def _engine(adapters: PostgresAdapters, *, checkpoints: Any):
        registry = ToolRegistry()
        registry.publish(
            (
                ToolDescriptor(
                    id="example.read",
                    version="1",
                    extension_id="example.echo",
                    risk=RiskLevel.READ,
                    input_schema={"type": "object", "additionalProperties": False},
                    output_schema={"type": "object", "additionalProperties": True},
                ),
            )
        )
        gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=ApprovalService(InMemoryApprovalRepository()),
            executor=_Executor(),
        )
        return AgentEngine(
            runs=adapters.run_repository,
            checkpoints=checkpoints,
            observations=adapters.observation_store,
            context=ContextManager(()),
            planner=_Planner(),
            validator=_Validator(),
            tools=gateway,
            registry=registry,
            unit_of_work=adapters.database,
        )

    @staticmethod
    def _audit_event(event_type: str, resource_id: str, data: dict[str, Any]):
        from personal_assistant.core.audit import AuditEvent

        return AuditEvent(event_type, "owner", "task", resource_id, data)

    @staticmethod
    def _extension_record(extension_id: str = "demo.weather") -> ExtensionRecord:
        manifest = ExtensionManifest(
            root=Path("."),
            manifest_version="1",
            id=extension_id,
            name="Weather",
            version="0.1.0",
            core_api=">=1.0,<2.0",
            python=">=3.12",
            entrypoint="demo_weather.worker:Extension",
            dependency_lock="requirements.lock",
            config_schema=None,
            state_schema_version=0,
            healthcheck="system.health",
            tools=(ManifestTool("demo.weather.get", "READ", "in.json", "out.json"),),
            capabilities=DeclaredCapabilities(required=("net.http",)),
        )
        return ExtensionRecord(
            manifest=manifest,
            artifact_hash="sha256:" + "b" * 64,
            state=ExtensionState.ENABLED,
            install_path="/tmp/demo_weather",
            data_retained=True,
        )
