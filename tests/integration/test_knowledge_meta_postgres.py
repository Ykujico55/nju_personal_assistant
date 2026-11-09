"""Knowledge metadata through the real host JSON codec and scoped SQL broker.

Only a dedicated *_test PostgreSQL endpoint is accepted. Each test creates and
removes its own database; no host app, original database, files or Worker runs.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from personal_knowledge import EXTENSION_VERSION
from personal_knowledge.binding import INDEX_BINDING_KEY, read_index_status
from personal_knowledge.store import KnowledgeStore

from personal_assistant.core.extensions.data_access import (
    HOST_DATA_EXECUTE,
    HOST_DATA_MIGRATE,
    HOST_DATA_TRANSACTION,
    DataAccessError,
    ExtensionDataContext,
)
from personal_assistant.infrastructure.database import (
    PostgresAdapterConfig,
    PostgresDatabase,
    PostgresExtensionDataAccess,
)
from personal_assistant.infrastructure.database.config import normalize_dsn

BASE_URL = os.getenv("PA_TEST_DATABASE_URL")
NAMESPACE = "ext_personal_2e_knowledge"


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, "", ""))


class _BrokerClient:
    def __init__(self, access: PostgresExtensionDataAccess) -> None:
        self.access = access
        self.context = ExtensionDataContext(
            extension_id="personal.knowledge",
            extension_version=EXTENSION_VERSION,
            namespace=NAMESPACE,
            payload_root=Path(__file__).resolve().parents[2] / "extensions/personal_knowledge",
        )

    async def execute(
        self,
        statement: str,
        parameters: Sequence[Any] = (),
        *,
        timeout_seconds: float = 30,
    ) -> Mapping[str, Any]:
        return cast(
            Mapping[str, Any],
            await self.access.handle(
                HOST_DATA_EXECUTE,
                {
                    "statement": statement,
                    "parameters": list(parameters),
                    "timeout_seconds": timeout_seconds,
                },
                context=self.context,
            ),
        )

    async def transaction(
        self,
        statements: Sequence[Mapping[str, Any]],
        *,
        timeout_seconds: float = 60,
    ) -> Mapping[str, Any]:
        return cast(
            Mapping[str, Any],
            await self.access.handle(
                HOST_DATA_TRANSACTION,
                {
                    "statements": list(statements),
                    "timeout_seconds": timeout_seconds,
                },
                context=self.context,
            ),
        )

    async def migrate(
        self,
        migrations: Sequence[Mapping[str, Any]],
        *,
        timeout_seconds: float = 60,
    ) -> Mapping[str, Any]:
        return cast(
            Mapping[str, Any],
            await self.access.handle(
                HOST_DATA_MIGRATE,
                {
                    "migrations": list(migrations),
                    "timeout_seconds": timeout_seconds,
                },
                context=self.context,
            ),
        )

    async def aclose(self) -> None:
        return None


@unittest.skipUnless(BASE_URL, "set PA_TEST_DATABASE_URL to an isolated *_test endpoint")
class KnowledgeMetaPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = normalize_dsn(BASE_URL or "")
        if not urlsplit(base).path.lstrip("/").endswith("_test"):
            raise RuntimeError("metadata test requires a dedicated *_test endpoint")
        self.admin_dsn = _with_database(base, "postgres")
        self.database_name = "pa_knowledge_meta_test_" + uuid.uuid4().hex
        admin = await asyncpg.connect(self.admin_dsn, timeout=10, command_timeout=10)
        try:
            await admin.execute(f'CREATE DATABASE "{self.database_name}"')
        finally:
            await admin.close()
        self.addAsyncCleanup(self._drop_database)
        self.database = PostgresDatabase(
            PostgresAdapterConfig(
                database_url=_with_database(base, self.database_name),
                pool_min_size=1,
                pool_max_size=2,
                command_timeout=10,
            )
        )
        self.addAsyncCleanup(self.database.close)
        # connect() installs production _init_connection JSON codecs; no core migrations.
        await self.database.connect()
        async with self.database.connection() as connection:
            await connection.execute("CREATE EXTENSION vector")
        self.client = _BrokerClient(PostgresExtensionDataAccess(self.database))
        await self.client.execute(
            "CREATE TABLE knowledge_meta (key text PRIMARY KEY, value jsonb NOT NULL)"
        )
        self.store = KnowledgeStore(self.client)

    async def _drop_database(self) -> None:
        admin = await asyncpg.connect(self.admin_dsn, timeout=10, command_timeout=10)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                self.database_name,
            )
            await admin.execute(f'DROP DATABASE "{self.database_name}"')
            self.assertIsNone(
                await admin.fetchval(
                    "SELECT datname FROM pg_database WHERE datname = $1",
                    self.database_name,
                )
            )
        finally:
            await admin.close()

    async def _seed(self, value: Any) -> None:
        # A Python string deliberately reproduces the old double encoding via the real codec.
        await self.client.execute(
            "INSERT INTO knowledge_meta (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            [INDEX_BINDING_KEY, value],
        )

    async def _stored_type(self) -> str:
        result = await self.client.execute(
            "SELECT jsonb_typeof(value) AS kind FROM knowledge_meta WHERE key = $1",
            [INDEX_BINDING_KEY],
        )
        return str(result["rows"][0]["kind"])

    def _binding(self, *, state: str = "PENDING", scan_id: str = "scan-old") -> dict[str, Any]:
        return {"schema": 1, "binding_hash": "a" * 64, "state": state, "scan_id": scan_id}

    async def test_new_metadata_is_json_object_and_round_trips_through_broker(self) -> None:
        value = self._binding(state="READY")
        await self.store.set_meta(INDEX_BINDING_KEY, value)
        self.assertEqual("object", await self._stored_type())
        self.assertEqual(value, await self.store.get_meta(INDEX_BINDING_KEY))
        self.assertEqual(
            {"index_state": "READY", "index_reason": "INDEX_READY"},
            await read_index_status(self.store, "a" * 64),
        )

    async def test_legacy_string_decodes_once_without_bypassing_binding_or_pending(self) -> None:
        pending = self._binding()
        await self._seed(json.dumps(pending, sort_keys=True))
        self.assertEqual("string", await self._stored_type())
        self.assertEqual(pending, await self.store.get_meta(INDEX_BINDING_KEY))
        self.assertEqual(
            {"index_state": "PENDING", "index_reason": "INDEX_SCAN_INCOMPLETE"},
            await read_index_status(self.store, "a" * 64),
        )
        ready = self._binding(state="READY")
        await self._seed(json.dumps(ready, sort_keys=True))
        self.assertEqual(
            {"index_state": "PENDING", "index_reason": "ROOT_BINDING_CHANGED"},
            await read_index_status(self.store, "b" * 64),
        )
        self.assertEqual("READY", (await read_index_status(self.store, "a" * 64))["index_state"])
        for unsupported in (json.dumps(json.dumps(ready, sort_keys=True)), "{bad", "[]", "null"):
            with self.subTest(unsupported=unsupported):
                await self._seed(unsupported)
                self.assertIsNone(await self.store.get_meta(INDEX_BINDING_KEY))
                self.assertEqual(
                    "INDEX_BINDING_MISSING",
                    (await read_index_status(self.store, "a" * 64))["index_reason"],
                )

    async def test_compare_set_accepts_legacy_and_object_but_writes_object(self) -> None:
        pending, ready = self._binding(), self._binding(state="READY")
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                await self._seed(json.dumps(pending, sort_keys=True) if legacy else pending)
                self.assertTrue(
                    await self.store.compare_set_meta(
                        INDEX_BINDING_KEY,
                        pending,
                        ready,
                    )
                )
                self.assertEqual("object", await self._stored_type())
                self.assertEqual(ready, await self.store.get_meta(INDEX_BINDING_KEY))
                self.assertFalse(
                    await self.store.compare_set_meta(
                        INDEX_BINDING_KEY,
                        pending,
                        ready,
                    )
                )
                self.assertEqual(ready, await self.store.get_meta(INDEX_BINDING_KEY))

    async def test_late_scan_cannot_promote_or_overwrite_new_scan(self) -> None:
        old, new = self._binding(), self._binding(scan_id="scan-new")
        await self.store.set_meta(INDEX_BINDING_KEY, old)
        await self.store.set_meta(INDEX_BINDING_KEY, new)
        self.assertFalse(
            await self.store.compare_set_meta(
                INDEX_BINDING_KEY,
                old,
                {**old, "state": "READY"},
            )
        )
        self.assertEqual(new, await self.store.get_meta(INDEX_BINDING_KEY))
        self.assertEqual(
            "INDEX_SCAN_INCOMPLETE", (await read_index_status(self.store, "a" * 64))["index_reason"]
        )
        ready = {**new, "state": "READY"}
        self.assertTrue(await self.store.compare_set_meta(INDEX_BINDING_KEY, new, ready))
        self.assertFalse(
            await self.store.compare_set_meta(
                INDEX_BINDING_KEY,
                old,
                {**old, "state": "READY"},
            )
        )
        self.assertEqual(ready, await self.store.get_meta(INDEX_BINDING_KEY))
        self.assertEqual("object", await self._stored_type())

    async def test_full_scan_empty_markdown_and_fts_keep_verified_source_locator(self) -> None:
        import tempfile

        from personal_knowledge.embedding import NullEmbeddingProvider
        from personal_knowledge.index import KnowledgeIndex
        from personal_knowledge.models import EvidenceStatus
        from personal_knowledge.paths import AuthorizedRoot
        from personal_knowledge.worker import MIGRATIONS

        with tempfile.TemporaryDirectory(prefix="pa_knowledge_meta_sources_") as directory:
            root = Path(directory).resolve()
            self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            body = "# 合成课程\n\n数据结构 二叉树 算法\n"
            (root / "course.md").write_text(body, encoding="utf-8")
            (root / "empty.md").write_text(" \n\t", encoding="utf-8")
            index = KnowledgeIndex(
                self.store,
                [AuthorizedRoot(key="synthetic", path=root)],
                NullEmbeddingProvider(),
                migrations=MIGRATIONS,
                max_file_bytes=1024 * 1024,
                extension_id="personal.knowledge",
                extension_version=EXTENSION_VERSION,
            )
            report = await index.reconcile(mode="full")
            self.assertFalse(report.errors)
            self.assertEqual(1, report.skipped_empty)
            self.assertEqual(
                {"index_state": "READY", "index_reason": "INDEX_READY"}, await index.index_status()
            )
            self.assertEqual("object", await self._stored_type())
            records = await self.store.workspace_index_records([
                ("synthetic", "course.md"), ("synthetic", "empty.md"), ("other", "course.md"),
            ])
            self.assertEqual({("synthetic", "course.md")}, set(records))
            record = records[("synthetic", "course.md")]
            self.assertEqual("READY", record["version_state"])
            self.assertTrue(record["active_version"])
            self.assertEqual(record["source_hash"], record["version_hash"])
            self.assertEqual((root / "course.md").stat().st_size, record["size_bytes"])
            self.assertEqual((root / "course.md").stat().st_mtime_ns, record["mtime_ns"])
            self.assertEqual({
                "root_key", "relative_path", "active_version", "source_hash", "size_bytes",
                "mtime_ns", "version_hash", "version_state",
            }, set(record))
            found = await index.search("数据结构", limit=5)
            self.assertEqual("disabled", found.vector_mode)
            results = await index.evaluate(found.results, found.scores)
            self.assertTrue(results)
            for result in results:
                self.assertEqual(EvidenceStatus.CURRENT, result.status)
                self.assertEqual("synthetic", result.record.root_key)
                self.assertEqual("course.md", result.record.relative_path)
                self.assertIn("数据结构", result.text)
                self.assertEqual("line_range", result.record.locator.kind)
                self.assertGreaterEqual(result.record.locator.start, 1)
            self.assertEqual(body, (root / "course.md").read_text(encoding="utf-8"))
            self.assertEqual(" \n\t", (root / "empty.md").read_text(encoding="utf-8"))


    async def test_index_build_lease_serializes_bindings_and_guards_ready(self) -> None:
        import asyncio
        claimed = await asyncio.gather(
            self.store.claim_build("first", "binding-one", 60),
            self.store.claim_build("second", "binding-two", 60),
        )
        self.assertEqual(sum(claimed), 1)
        owner = "first" if claimed[0] else "second"
        other = "second" if claimed[0] else "first"
        pending = {"schema": 1, "binding_hash": "test", "scan_id": owner, "state": "PENDING"}
        self.assertFalse(
            await self.store.set_meta_under_build_lease(INDEX_BINDING_KEY, pending, other)
        )
        self.assertTrue(
            await self.store.set_meta_under_build_lease(INDEX_BINDING_KEY, pending, owner)
        )
        self.assertTrue(await self.store.renew_build(owner, 60))
        self.assertFalse(await self.store.renew_build(other, 60))
        ready = dict(pending, state="READY")
        self.assertFalse(
            await self.store.compare_set_meta(INDEX_BINDING_KEY, pending, ready, lease_owner=other)
        )
        self.assertTrue(
            await self.store.compare_set_meta(INDEX_BINDING_KEY, pending, ready, lease_owner=owner)
        )
        self.assertFalse(await self.store.finish_build(other, "DONE"))
        self.assertTrue(await self.store.finish_build(owner, "DONE"))

    async def test_expired_and_failed_index_leases_never_reuse_old_owner(self) -> None:
        self.assertTrue(await self.store.claim_build("old", "before", 60))
        pending = {"schema": 1, "binding_hash": "before", "scan_id": "old", "state": "PENDING"}
        self.assertTrue(
            await self.store.set_meta_under_build_lease(INDEX_BINDING_KEY, pending, "old")
        )
        await self.client.execute(
            "UPDATE knowledge_meta SET value = jsonb_set(value, '{expires_at}', '0'::jsonb) "
            "WHERE key = 'index.build.lease.v1'"
        )
        self.assertFalse(await self.store.renew_build("old", 60))
        self.assertFalse(
            await self.store.compare_set_meta(
                INDEX_BINDING_KEY, pending, dict(pending, state="READY"), lease_owner="old"
            )
        )
        self.assertTrue(await self.store.claim_build("new", "after", 60))
        self.assertFalse(await self.store.finish_build("old", "DONE"))
        self.assertTrue(await self.store.finish_build("new", "FAILED"))
        self.assertFalse(await self.store.claim_build("auto", "after", 60))
        self.assertTrue(await self.store.claim_build("manual", "after", 60, retry_failed=True))


    async def test_expired_build_view_cannot_mutate_and_failed_postguard_rolls_back(self) -> None:
        await self.client.execute("CREATE TABLE build_probe (id text PRIMARY KEY)")
        self.assertTrue(await self.store.claim_build("old", "binding-a", 60))
        scoped = self.store.with_build_lease("old")
        await scoped._data.execute("INSERT INTO build_probe VALUES ('kept')")
        with self.assertRaises(DataAccessError) as failure:
            await scoped._data.transaction([
                {"statement": "DELETE FROM build_probe", "parameters": []},
                {"statement": "UPDATE knowledge_meta SET value = "
                 "jsonb_set(value, '{expires_at}', '0'::jsonb) "
                 "WHERE key = 'index.build.lease.v1'", "parameters": []},
            ])
        self.assertEqual("DATA_STATEMENT_REJECTED", failure.exception.code)
        self.assertIsInstance(failure.exception.__cause__, asyncpg.exceptions.DivisionByZeroError)
        result = await self.client.execute("SELECT id FROM build_probe")
        self.assertEqual([{"id": "kept"}], result["rows"])
        self.assertTrue(await self.store.renew_build("old", 60))
        await self.client.execute(
            "UPDATE knowledge_meta SET value = jsonb_set(value, '{expires_at}', '0'::jsonb) "
            "WHERE key = 'index.build.lease.v1'"
        )
        self.assertTrue(await self.store.claim_build("new", "binding-b", 60))
        with self.assertRaises(DataAccessError) as failure:
            await scoped._data.execute("DELETE FROM build_probe")
        self.assertEqual("DATA_STATEMENT_REJECTED", failure.exception.code)
        self.assertIsInstance(failure.exception.__cause__, asyncpg.exceptions.DivisionByZeroError)
        result = await self.client.execute("SELECT id FROM build_probe")
        self.assertEqual([{"id": "kept"}], result["rows"])
        self.assertEqual("new", (await self.store.get_meta("index.build.lease.v1"))["owner"])


    async def _run_after_lease_lock_expiry(self, operation, *, waiters=1):
        import asyncio

        blocker = await asyncpg.connect(_with_database(self.admin_dsn, self.database_name))
        tx = blocker.transaction()
        task = None
        await tx.start()
        try:
            lease = await blocker.fetchrow(
                f'SELECT value FROM "{NAMESPACE}".knowledge_meta '
                "WHERE key = 'index.build.lease.v1' FOR UPDATE"
            )
            expiry = json.loads(lease["value"])["expires_at"]
            task = asyncio.create_task(operation())
            async with asyncio.timeout(5):
                while True:
                    await blocker.execute("SELECT pg_stat_clear_snapshot()")
                    waiting = await blocker.fetchval(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=$1 "
                        "AND pid <> pg_backend_pid() AND wait_event_type='Lock'",
                        self.database_name,
                    )
                    if waiting >= waiters:
                        break
                    await asyncio.sleep(0.01)
            self.assertTrue(await blocker.fetchval(
                "SELECT extract(epoch from clock_timestamp()) < $1::double precision", expiry
            ), "fixture must reach the row lock before expiry")
            async with asyncio.timeout(5):
                while not await blocker.fetchval(
                    "SELECT extract(epoch from clock_timestamp()) >= $1::double precision", expiry
                ):
                    await asyncio.sleep(0.02)
            await tx.rollback()
            return await asyncio.wait_for(task, 5)
        finally:
            await blocker.close()
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_waiting_renewal_cannot_resurrect_expired_owner(self) -> None:
        self.assertTrue(await self.store.claim_build("old", "binding", 3))
        renewed = await self._run_after_lease_lock_expiry(
            lambda: self.store.renew_build("old", 60)
        )
        self.assertFalse(renewed)
        self.assertTrue(await self.store.claim_build("new", "binding", 60))
        self.assertFalse(await self.store.renew_build("old", 60))
        self.assertFalse(await self.store.finish_build("old", "DONE"))
        self.assertTrue(await self.store.set_meta_under_build_lease("probe", {"new": True}, "new"))
        with self.assertRaises(DataAccessError):
            await self.store.with_build_lease("old").set_meta("probe", {"old": True})
        self.assertEqual({"new": True}, await self.store.get_meta("probe"))

    async def test_metadata_publish_waiting_for_lease_checks_time_after_lock(self) -> None:
        self.assertTrue(await self.store.claim_build("owner", "binding", 3))
        published = await self._run_after_lease_lock_expiry(
            lambda: self.store.set_meta_under_build_lease("probe", {"state": "READY"}, "owner")
        )
        self.assertFalse(published)
        self.assertIsNone(await self.store.get_meta("probe"))

    async def test_metadata_cas_waiting_for_lease_checks_time_after_lock(self) -> None:
        await self.store.set_meta("probe", {"state": "PENDING"})
        self.assertTrue(await self.store.claim_build("owner", "binding", 3))
        changed = await self._run_after_lease_lock_expiry(
            lambda: self.store.compare_set_meta(
                "probe", {"state": "PENDING"}, {"state": "READY"}, lease_owner="owner"
            )
        )
        self.assertFalse(changed)
        self.assertEqual({"state": "PENDING"}, await self.store.get_meta("probe"))

    async def test_waiting_claim_uses_acquisition_time_for_new_lease(self) -> None:
        self.assertTrue(await self.store.claim_build("old", "binding", 3))
        claimed = await self._run_after_lease_lock_expiry(
            lambda: self.store.claim_build("new", "new-binding", 60)
        )
        self.assertTrue(claimed)
        self.assertTrue(await self.store.renew_build("new", 60))
        self.assertFalse(await self.store.renew_build("old", 60))

    async def test_build_liveness_is_evaluated_by_database_clock(self) -> None:
        self.assertIsNone(await self.store.get_build_lease())
        self.assertTrue(await self.store.claim_build("owner", "binding", 60))
        self.assertTrue((await self.store.get_build_lease())["active"])
        await self.client.execute(
            "UPDATE knowledge_meta SET value=jsonb_set(value, '{expires_at}', '0'::jsonb) "
            "WHERE key='index.build.lease.v1'"
        )
        lease = await self.store.get_build_lease()
        self.assertEqual("RUNNING", lease["state"])
        self.assertFalse(lease["active"])


    async def test_expired_renewal_and_successor_claim_compete_without_revival(self) -> None:
        import asyncio

        self.assertTrue(await self.store.claim_build("old", "binding-a", 3))

        async def compete():
            return await asyncio.gather(
                self.store.renew_build("old", 60),
                self.store.claim_build("successor", "binding-b", 60),
            )

        renewed, claimed = await self._run_after_lease_lock_expiry(compete, waiters=2)
        self.assertFalse(renewed)
        self.assertTrue(claimed)
        lease = await self.store.get_build_lease()
        self.assertEqual("successor", lease["owner"])
        self.assertTrue(lease["active"])
        self.assertFalse(await self.store.finish_build("old", "DONE"))
        with self.assertRaises(DataAccessError):
            await self.store.with_build_lease("old").set_meta("probe", {"old": True})
        self.assertTrue(await self.store.set_meta_under_build_lease(
            "probe", {"successor": True}, "successor"
        ))
        self.assertEqual({"successor": True}, await self.store.get_meta("probe"))
