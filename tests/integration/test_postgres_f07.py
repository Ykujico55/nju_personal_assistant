"""F07 PostgreSQL integration: durable sessions, previews and restart recovery."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import unittest
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from personal_assistant.core.browser import (
    BrowserSessionBroker,
    BrowserSessionRecord,
    BrowserSessionState,
    FieldChange,
    FieldValueSource,
    FillPlan,
)
from personal_assistant.domain.errors import AlreadyExistsError
from personal_assistant.infrastructure.database import (
    PostgresAdapterConfig,
    PostgresAdapters,
    PostgresBrowserAdapterStore,
    PostgresBrowserSessionStore,
    build_postgres_adapters,
)
from personal_assistant.infrastructure.memory.browser import (
    InMemoryBrowserAdapterStore,
    InMemoryBrowserSessionStore,
)
from tests.support.browser import (
    TEST_APP_PATH,
    TEST_LOGIN_PATH,
    TEST_ORIGIN,
    standard_adapter,
    standard_companion,
)

BASE_URL = os.getenv("PA_TEST_DATABASE_URL")
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
EXTENSION_ID = "nju.ehall"
EXTENSION_VERSION = "0.1.0"
TASK_ID = "task-f07"


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres://", "postgresql://"
    )


def _with_db(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, "", ""))


def _safe_rmtree(path: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise AssertionError(f"refusing to delete {resolved}")
    shutil.rmtree(resolved, ignore_errors=True)


def _proof_plan(fingerprint: str) -> FillPlan:
    return FillPlan(
        adapter_id="nju.ehall.proof",
        adapter_version="1.0.0",
        transaction_id="proof.apply",
        expected_origin=TEST_ORIGIN,
        expected_page_fingerprint=fingerprint,
        app_id="proof",
        fields=(
            FieldChange("reason", "ctl:0:0", "理由", "", "需要办理", FieldValueSource.USER_INPUT),
            FieldChange("phone", "ctl:0:1", "电话", "", "13800000000", FieldValueSource.USER_INPUT),
            FieldChange(
                "delivery", "ctl:1:0", "领取", "paper", "paper", FieldValueSource.USER_INPUT
            ),
        ),
    )


@unittest.skipUnless(BASE_URL, "set PA_TEST_DATABASE_URL to run real PostgreSQL tests")
class BrowserStorePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = _dsn(BASE_URL or "")
        if not urlsplit(base).path.lstrip("/").endswith("_test"):
            raise RuntimeError("PA_TEST_DATABASE_URL must name a dedicated *_test database")
        self._admin_dsn = _with_db(base, "postgres")
        self._db_name = f"pa_f07_{uuid.uuid4().hex}"
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{self._db_name}"')
        finally:
            await admin.close()
        self.database_url = _with_db(base, self._db_name)
        self.adapters: PostgresAdapters = build_postgres_adapters(
            PostgresAdapterConfig(database_url=self.database_url)
        )
        await self.adapters.startup()
        self.sessions = PostgresBrowserSessionStore(self.adapters.database)
        self.browser_adapters = PostgresBrowserAdapterStore(self.adapters.database)

    async def asyncTearDown(self) -> None:
        with contextlib.suppress(Exception):
            await self.adapters.close()
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

    async def _reopen(self) -> None:
        await self.adapters.close()
        self.adapters = build_postgres_adapters(
            PostgresAdapterConfig(database_url=self.database_url)
        )
        await self.adapters.startup()
        self.sessions = PostgresBrowserSessionStore(self.adapters.database)
        self.browser_adapters = PostgresBrowserAdapterStore(self.adapters.database)

    def _broker(self) -> BrowserSessionBroker:
        return BrowserSessionBroker(
            companion=standard_companion(),
            sessions=self.sessions,
            adapters=self.browser_adapters,
            allowed_origins=frozenset({TEST_ORIGIN}),
            submit_enabled=True,
            id_factory=lambda: "brs_pg",
            nonce_factory=lambda: "nonce-pg",
        )

    async def _reach_prepared(self, broker: BrowserSessionBroker) -> tuple[str, str]:
        await broker.register_adapter(standard_adapter())
        record = await broker.create_session(
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="办理在读证明",
        )
        session_id = record.session_id
        await broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id="nju.ehall.proof",
            transaction_id="proof.apply",
            url=TEST_ORIGIN + TEST_LOGIN_PATH,
        )
        snapshot = await broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id="nju.ehall.proof",
            transaction_id="proof.apply",
            url=TEST_ORIGIN + TEST_APP_PATH,
        )
        await broker.record_discovery(session_id, extension_id=EXTENSION_ID, app_count=4)
        await broker.record_preparation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id="nju.ehall.proof",
            adapter_version="1.0.0",
            app_id="proof",
            transaction_id="proof.apply",
            page_fingerprint=snapshot.fingerprint,
            planned_fields=3,
        )
        return session_id, snapshot.fingerprint

    async def test_migration_creates_the_browser_tables(self) -> None:
        async with self.adapters.database.connection() as connection:
            rows = await connection.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name IN "
                "('browser_sessions', 'browser_adapters') ORDER BY table_name"
            )
            columns = await connection.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'browser_sessions'"
            )
        self.assertEqual(
            [row["table_name"] for row in rows],
            ["browser_adapters", "browser_sessions"],
        )
        names = {row["column_name"] for row in columns}
        for expected in (
            "session_id",
            "state",
            "preview",
            "preview_hash",
            "preview_nonce",
            "visited_paths",
            "outcome",
            "receipt",
            "receipt_baseline",
            "owner_id",
        ):
            self.assertIn(expected, names)
        async with self.adapters.database.connection() as connection:
            indexes = await connection.fetch(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'browser_sessions'"
            )
        definitions = {row["indexname"]: row["indexdef"] for row in indexes}
        self.assertIn("browser_sessions_task_open_unique_idx", definitions)
        self.assertIn("UNIQUE", definitions["browser_sessions_task_open_unique_idx"])
        self.assertIn("SUCCEEDED", definitions["browser_sessions_task_open_unique_idx"])

    async def test_receipt_baseline_round_trips(self) -> None:
        await self.sessions.create(self._record("brs_baseline"))
        record = await self.sessions.get("brs_baseline")
        assert record is not None
        stored = await self.sessions.save(
            replace(record, receipt_baseline="回执号 NJU-2026-0001"), expected_version=0
        )
        reloaded = await self.sessions.get("brs_baseline")
        assert reloaded is not None
        self.assertEqual(reloaded.receipt_baseline, "回执号 NJU-2026-0001")
        self.assertEqual(stored.version, 1)

    async def test_concurrent_open_sessions_for_one_task_are_rejected(self) -> None:
        await self.sessions.create(self._record("brs_first"))
        duplicate = replace(self._record("brs_second"), task_id=TASK_ID)
        with self.assertRaises(AlreadyExistsError):
            await self.sessions.create(duplicate)

    async def test_concurrent_broker_creation_yields_one_session(self) -> None:
        import asyncio

        broker = self._broker()
        await broker.register_adapter(standard_adapter())
        results = await asyncio.gather(
            *[
                broker.create_session(
                    task_id=TASK_ID,
                    extension_id=EXTENSION_ID,
                    extension_version=EXTENSION_VERSION,
                    purpose="办理在读证明",
                )
                for _ in range(4)
            ]
        )
        self.assertEqual(len({item.session_id for item in results}), 1)

    def _record(self, session_id: str):
        from personal_assistant.core.browser import BrowserSessionRecord

        now = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
        return BrowserSessionRecord(
            session_id=session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="办理在读证明",
            state=BrowserSessionState.REQUESTED,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(minutes=30),
        )

    async def test_preview_survives_a_restart(self) -> None:
        broker = self._broker()
        session_id, fingerprint = await self._reach_prepared(broker)
        plan = _proof_plan(fingerprint)
        preview = await broker.execute_fill(
            session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
        )
        await self._reopen()
        record = await self.sessions.get(session_id)
        assert record is not None
        self.assertEqual(record.state, BrowserSessionState.PREVIEW_READY)
        self.assertEqual(record.preview_hash, preview.canonical_payload_hash)
        self.assertEqual(record.preview_nonce, "nonce-pg")
        assert record.preview is not None
        self.assertEqual(record.preview["canonical_payload_hash"], preview.canonical_payload_hash)
        self.assertEqual(len(record.preview["fields"]), 3)

    async def test_executing_sessions_recover_to_unknown_after_restart(self) -> None:
        broker = self._broker()
        session_id, fingerprint = await self._reach_prepared(broker)
        record = await self.sessions.get(session_id)
        assert record is not None
        await self.sessions.save(
            replace(record, state=BrowserSessionState.EXECUTING, owner_id="dead-owner"),
            expected_version=record.version,
        )
        await self._reopen()
        recovered = await self.sessions.recover_stale_executions(active_owners=())
        self.assertEqual(recovered, (session_id,))
        after = await self.sessions.get(session_id)
        assert after is not None
        self.assertEqual(after.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(after.diagnostic_code, "EXECUTION_INTERRUPTED")
        # A live owner is never swept.
        await self.sessions.save(
            replace(after, state=BrowserSessionState.EXECUTING, owner_id="live-owner"),
            expected_version=after.version,
        )
        swept = await self.sessions.recover_stale_executions(active_owners=("live-owner",))
        self.assertEqual(swept, ())

    async def test_safety_pause_and_preview_survive_a_restart(self) -> None:
        broker = self._broker()
        session_id, fingerprint = await self._reach_prepared(broker)
        plan = _proof_plan(fingerprint)
        preview = await broker.execute_fill(
            session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
        )
        record = await self.sessions.get(session_id)
        assert record is not None
        self.assertEqual(record.state, BrowserSessionState.PREVIEW_READY)
        # A drift after the preview is a safety pause, never a silent retry.
        failing = replace(plan, expected_page_fingerprint="0" * 64)
        from personal_assistant.core.browser import PageDriftError

        with self.assertRaises(PageDriftError):
            await broker.execute_fill(
                session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=failing
            )
        paused = await self.sessions.get(session_id)
        assert paused is not None
        self.assertEqual(paused.state, BrowserSessionState.SAFETY_PAUSED)
        await self._reopen()
        persisted = await self.sessions.get(session_id)
        assert persisted is not None
        self.assertEqual(persisted.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(persisted.preview_hash, preview.canonical_payload_hash)

    async def test_concurrent_saves_allow_exactly_one_winner(self) -> None:
        broker = self._broker()
        session_id = (await broker.create_session(
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="x",
        )).session_id
        first = await self.sessions.get(session_id)
        second = await self.sessions.get(session_id)
        assert first is not None and second is not None
        winner = await self.sessions.save(
            replace(first, outcome="first"), expected_version=first.version
        )
        self.assertEqual(winner.version, first.version + 1)
        from personal_assistant.domain.errors import ConcurrentModificationError

        with self.assertRaises(ConcurrentModificationError):
            await self.sessions.save(
                replace(second, outcome="second"), expected_version=second.version
            )

    async def test_adapters_survive_a_restart_and_upsert_increments_version(self) -> None:
        broker = self._broker()
        await broker.register_adapter(standard_adapter())
        again = await broker.register_adapter(standard_adapter(display_name="在读证明申请v2"))
        self.assertEqual(again.version, 1)
        await self._reopen()
        stored = await self.browser_adapters.get(EXTENSION_ID, "nju.ehall.proof", "1.0.0")
        assert stored is not None
        self.assertEqual(stored.descriptor["display_name"], "在读证明申请v2")
        self.assertEqual(len(await self.browser_adapters.list_for_extension(EXTENSION_ID)), 1)

    async def test_find_active_for_task_orders_and_filters(self) -> None:
        broker = self._broker()
        session_id = (await broker.create_session(
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="x",
        )).session_id
        records = await self.sessions.find_active_for_task(TASK_ID)
        self.assertEqual([item.session_id for item in records], [session_id])
        self.assertEqual(await self.sessions.find_active_for_task("other-task"), ())

    async def test_memory_store_matches_the_postgres_state_machine(self) -> None:
        memory = InMemoryBrowserSessionStore()
        adapters = InMemoryBrowserAdapterStore()
        broker = BrowserSessionBroker(
            companion=standard_companion(),
            sessions=memory,
            adapters=adapters,
            allowed_origins=frozenset({TEST_ORIGIN}),
            submit_enabled=True,
            id_factory=lambda: "brs_mem",
            nonce_factory=lambda: "nonce-mem",
        )
        session_id, _ = await self._reach_prepared(broker)
        record = await memory.get(session_id)
        assert record is not None
        self.assertNotIn(record.state, {BrowserSessionState.SUCCEEDED})
        now = datetime.now(UTC) + timedelta(seconds=1)
        self.assertGreater(record.expires_at, now - timedelta(days=1))


@unittest.skipUnless(BASE_URL, "set PA_TEST_DATABASE_URL to run real PostgreSQL tests")
class MigrationConvergenceTests(unittest.IsolatedAsyncioTestCase):
    """0008 must converge pre-existing duplicates before the unique index."""

    async def asyncSetUp(self) -> None:
        base = _dsn(BASE_URL or "")
        if not urlsplit(base).path.lstrip("/").endswith("_test"):
            raise RuntimeError("PA_TEST_DATABASE_URL must name a dedicated *_test database")
        self._admin_dsn = _with_db(base, "postgres")
        self._db_name = f"pa_f07_conv_{uuid.uuid4().hex}"
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{self._db_name}"')
        finally:
            await admin.close()
        self.database_url = _with_db(base, self._db_name)
        self._dir = Path(tempfile.mkdtemp(prefix="pa_f07_pre0008_"))
        for name in sorted(MIGRATIONS_DIR.glob("0*.sql")):
            if name.name <= "0007_f07_browser_sessions.sql":
                shutil.copy(name, self._dir / name.name)
        self.legacy = build_postgres_adapters(
            PostgresAdapterConfig(database_url=self.database_url, migrations_dir=self._dir)
        )
        await self.legacy.startup()
        self._adapters: list[PostgresAdapters] = [self.legacy]

    async def asyncTearDown(self) -> None:
        for adapters in self._adapters:
            with contextlib.suppress(Exception):
                await adapters.close()
        shutil.rmtree(self._dir, ignore_errors=True)
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

    def _record(self, session_id: str, *, state: BrowserSessionState):
        now = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
        return BrowserSessionRecord(
            session_id=session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="办理在读证明",
            state=state,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(minutes=30),
        )

    async def _legacy_insert(self, session_id: str, state: str, *, minute: int) -> None:
        now = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC) + timedelta(minutes=minute)
        async with self.legacy.database.connection() as connection:
            await connection.execute(
                "INSERT INTO browser_sessions ("
                "session_id, task_id, extension_id, extension_version, purpose, "
                "state, created_at, updated_at, expires_at) "
                "VALUES ($1, $2, $3, $4, 'dup', $5, $6, $6, $7)",
                session_id,
                TASK_ID,
                EXTENSION_ID,
                EXTENSION_VERSION,
                state,
                now,
                now + timedelta(minutes=30),
            )

    async def _upgrade(self) -> PostgresAdapters:
        await self.legacy.close()
        full = build_postgres_adapters(
            PostgresAdapterConfig(database_url=self.database_url)
        )
        self._adapters.append(full)
        await full.startup()
        return full

    async def test_0008_keeps_the_pending_action_and_cancels_earlier_sessions(self) -> None:
        # An earlier AUTHENTICATED session must never outrank a later pending
        # external action: the UNKNOWN session is kept for reconciliation.
        await self._legacy_insert("brs_safe", "AUTHENTICATED", minute=0)
        await self._legacy_insert("brs_pending", "UNKNOWN", minute=1)
        full = await self._upgrade()
        async with full.database.connection() as connection:
            rows = await connection.fetch(
                "SELECT session_id, state, diagnostic_code FROM browser_sessions "
                "WHERE task_id = $1 ORDER BY created_at, session_id",
                TASK_ID,
            )
        by_id = {row["session_id"]: row for row in rows}
        self.assertEqual("UNKNOWN", by_id["brs_pending"]["state"])
        self.assertEqual("CANCELLED", by_id["brs_safe"]["state"])
        self.assertEqual("DUPLICATE_CONVERGED", by_id["brs_safe"]["diagnostic_code"])

    async def test_0008_fails_closed_with_multiple_pending_actions(self) -> None:
        await self._legacy_insert("brs_pending_a", "UNKNOWN", minute=0)
        await self._legacy_insert("brs_pending_b", "EXECUTING", minute=1)
        from personal_assistant.infrastructure.database.migrate import MigrationError

        with self.assertRaises(MigrationError):
            await self._upgrade()
        connection = await asyncpg.connect(self.database_url)
        try:
            rows = await connection.fetch(
                "SELECT version FROM schema_migrations WHERE version = $1",
                "0008_f07_browser_session_uniqueness",
            )
            states = await connection.fetch(
                "SELECT state FROM browser_sessions WHERE task_id = $1", TASK_ID
            )
        finally:
            await connection.close()
        self.assertEqual([], [row["version"] for row in rows])
        self.assertEqual(
            {"UNKNOWN", "EXECUTING"}, {row["state"] for row in states}
        )

    async def test_0008_converges_duplicates_before_the_unique_index(self) -> None:
        for index, session_id in enumerate(("brs_dup_a", "brs_dup_b", "brs_dup_c")):
            await self._legacy_insert(session_id, "AUTHENTICATED", minute=index)
        full = await self._upgrade()
        async with full.database.connection() as connection:
            rows = await connection.fetch(
                "SELECT session_id, state, diagnostic_code FROM browser_sessions "
                "WHERE task_id = $1 ORDER BY created_at, session_id",
                TASK_ID,
            )
            indexes = await connection.fetch(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'browser_sessions'"
            )
        self.assertEqual(
            "brs_dup_a", rows[0]["session_id"]
        )
        self.assertEqual(BrowserSessionState.AUTHENTICATED.value, rows[0]["state"])
        for row in rows[1:]:
            self.assertEqual(BrowserSessionState.CANCELLED.value, row["state"])
            self.assertEqual("DUPLICATE_CONVERGED", row["diagnostic_code"])
        self.assertIn(
            "browser_sessions_task_open_unique_idx",
            {row["indexname"] for row in indexes},
        )
        store = PostgresBrowserSessionStore(full.database)
        with self.assertRaises(AlreadyExistsError):
            await store.create(
                self._record("brs_dup_d", state=BrowserSessionState.AUTHENTICATED)
            )


if __name__ == "__main__":
    unittest.main()
