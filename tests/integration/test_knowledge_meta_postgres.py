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
