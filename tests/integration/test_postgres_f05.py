"""F05: real PostgreSQL 17 + pgvector acceptance for the knowledge extension.

Covers: extension migration execution through the generic host data capability,
side-by-side builds with atomic activation, FTS + vector + hybrid retrieval,
golden query recall, citation verification, deletion propagation, concurrency
and path safety against the real filesystem.  Everything runs on throwaway
``*_test`` databases and temporary directories with synthetic fixtures.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import os
import shutil
import tempfile
import unittest
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from personal_knowledge.embedding import DeterministicTestEmbeddingProvider, NullEmbeddingProvider
from personal_knowledge.index import KnowledgeIndex
from personal_knowledge.paths import parse_roots, resolve_roots
from personal_knowledge.store import KnowledgeStore
from personal_knowledge.worker import MIGRATIONS, PersonalKnowledgeExtension

from personal_assistant.core.extensions.data_access import (
    HOST_DATA_EXECUTE,
    HOST_DATA_MIGRATE,
    HOST_DATA_TRANSACTION,
    DataAccessError,
    ExtensionDataContext,
)
from personal_assistant.infrastructure.database import (
    PostgresAdapterConfig,
    PostgresAdapters,
    PostgresDatabase,
    PostgresExtensionDataAccess,
    build_postgres_adapters,
)

BASE_URL = os.getenv("PA_TEST_DATABASE_URL")
EXTENSION_ID = "personal.knowledge"
EXTENSION_VERSION = "0.1.0"
NAMESPACE = "ext_personal_2e_knowledge"


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres://", "postgresql://"
    )


def _with_db(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, "", ""))


class _BrokerClient:
    """Worker-side HostDataClient implemented in-process for integration tests."""

    def __init__(self, access: PostgresExtensionDataAccess, context: ExtensionDataContext) -> None:
        self._access = access
        self._context = context

    async def execute(
        self, statement: str, parameters: Sequence[Any] = (), *, timeout_seconds: float = 30.0
    ) -> Mapping[str, Any]:
        return await self._access.handle(
            HOST_DATA_EXECUTE,
            {
                "statement": statement,
                "parameters": list(parameters),
                "timeout_seconds": timeout_seconds,
            },
            context=self._context,
        )

    async def transaction(
        self, statements: Sequence[Mapping[str, Any]], *, timeout_seconds: float = 60.0
    ) -> Mapping[str, Any]:
        return await self._access.handle(
            HOST_DATA_TRANSACTION,
            {"statements": [dict(item) for item in statements], "timeout_seconds": timeout_seconds},
            context=self._context,
        )

    async def migrate(
        self, migrations: Sequence[Mapping[str, Any]], *, timeout_seconds: float = 60.0
    ) -> Mapping[str, Any]:
        return await self._access.handle(
            HOST_DATA_MIGRATE,
            {"migrations": [dict(item) for item in migrations], "timeout_seconds": timeout_seconds},
            context=self._context,
        )

    async def aclose(self) -> None:
        return None


GOLDEN_FILES: dict[str, str] = {
    "research.md": "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n",
    "budget.txt": "Quarterly budget review totals 4230 credits for lab equipment.\n",
    "recipe.md": "# Sourdough\n\nStarter feeding schedule repeats every 12 hours.\n",
    "travel.md": "# Trip\n\nFlight MU5137 departs terminal 2 at 09:40.\n",
    "health.txt": "Allergy medication list includes cetirizine tablets.\n",
    "notes/meeting.md": "# Vendor\n\nVendor contract renewal owner is Dana Whitfield.\n",
    "notes/ideas.md": "# Solar\n\nSolar panel installation quote reference SP-2214.\n",
    "recipes2.md": "# Pizza\n\nPizza dough hydration target is 65 percent.\n",
}

GOLDEN_QUERIES: tuple[tuple[str, str], ...] = (
    ("halcyon deadline", "research.md"),
    ("quarterly budget 4230 credits", "budget.txt"),
    ("sourdough starter feeding schedule", "recipe.md"),
    ("MU5137 terminal departs", "travel.md"),
    ("cetirizine allergy medication", "health.txt"),
    ("Dana Whitfield renewal", "notes/meeting.md"),
    ("SP-2214 solar panel", "notes/ideas.md"),
    ("pizza dough hydration", "recipes2.md"),
    ("halcyon project deadline 2026", "research.md"),
    ("terminal 2 flight", "travel.md"),
    ("starter feeding 12 hours", "recipe.md"),
    ("contract owner vendor", "notes/meeting.md"),
)


def _pdf_bytes(pages: list[str]) -> bytes:
    import zlib

    objects: dict[int, bytes] = {}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{3 + index} 0 R" for index in range(len(pages)))
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    contents_number = 3 + len(pages)
    for index in range(len(pages)):
        objects[3 + index] = (
            f"<< /Type /Page /Parent 2 0 R /Contents {contents_number + index} 0 R >>"
        ).encode()
    for index, text in enumerate(pages):
        body = zlib.compress(text.encode("latin-1"))
        header = f"<< /Length {len(body)} /Filter /FlateDecode >>".encode()
        objects[contents_number + index] = header + b"\nstream\n" + body + b"\nendstream"
    output = bytearray(b"%PDF-1.4\n")
    for number in sorted(objects):
        output += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    output += b"trailer\n<< /Size 10 >>\n%%EOF\n"
    return bytes(output)


@unittest.skipUnless(BASE_URL, "set PA_TEST_DATABASE_URL to run real PostgreSQL tests")
class PostgresF05Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = _dsn(BASE_URL or "")
        base_name = urlsplit(base).path.lstrip("/")
        if not base_name.endswith("_test"):
            raise RuntimeError("PA_TEST_DATABASE_URL must name a dedicated *_test database")
        self._admin_dsn = _with_db(base, "postgres")
        self._db_name = f"pa_f05_test_{uuid.uuid4().hex}"
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{self._db_name}"')
        finally:
            await admin.close()
        self.database_url = _with_db(base, self._db_name)
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f05_pg_"))
        self.root = self.tmp / "notes"
        self.root.mkdir()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self._adapters: list[PostgresAdapters] = []
        self._extra_databases: list[PostgresDatabase] = []
        self.adapters = await self._new_adapters()
        self.access = PostgresExtensionDataAccess(self.adapters.database)
        self.context = ExtensionDataContext(
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            namespace=NAMESPACE,
            payload_root=Path(__file__).resolve().parents[2] / "extensions" / "personal_knowledge",
        )
        self.client = _BrokerClient(self.access, self.context)

    async def _new_adapters(self) -> PostgresAdapters:
        adapters = build_postgres_adapters(
            PostgresAdapterConfig(database_url=self.database_url)
        )
        await adapters.startup()
        self._adapters.append(adapters)
        return adapters

    async def asyncTearDown(self) -> None:
        for adapters in self._adapters:
            with contextlib.suppress(Exception):
                await adapters.close()
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
        resolved = self.tmp.resolve()
        if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise AssertionError(f"refusing to delete {resolved}")
        shutil.rmtree(resolved, ignore_errors=True)

    # -- helpers ------------------------------------------------------------

    def index(
        self,
        *,
        embedder: Any = None,
        max_file_bytes: int = 4 * 1024 * 1024,
        roots: list[Path] | None = None,
        root_keys: list[str] | None = None,
    ) -> KnowledgeIndex:
        paths = roots or [self.root]
        keys = root_keys or [f"root-{number}" for number in range(len(paths))]
        specs = parse_roots(
            [
                {"path": str(path), "key": key}
                for path, key in zip(paths, keys, strict=True)
            ]
        )
        return KnowledgeIndex(
            KnowledgeStore(self.client),
            resolve_roots(specs),
            embedder or NullEmbeddingProvider(),
            migrations=MIGRATIONS,
            max_file_bytes=max_file_bytes,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
        )

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    def write_golden_corpus(self) -> None:
        for relative, content in GOLDEN_FILES.items():
            self.write(relative, content)
        (self.root / "event.pdf").write_bytes(
            _pdf_bytes(["BT /F1 12 Tf 72 720 Td (Conference badge number NB-7781) Tj ET"])
        )

    def extension(self, *, config: dict[str, Any]) -> PersonalKnowledgeExtension:

        extension = PersonalKnowledgeExtension()
        return extension

    async def _runtime(self, *, embedding: dict[str, Any] | None = None) -> Any:
        from personal_assistant_sdk import RuntimeContext

        return RuntimeContext(
            protocol_version="1",
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            data_namespace=NAMESPACE,
            manifest_schema_hash="",
            non_secret_config={
                "roots": [{"path": str(self.root), "key": "notes"}],
                **({"embedding": embedding} if embedding else {}),
            },
            host_data=self.client,
        )

    async def _sql(self, statement: str, *parameters: Any) -> list[Mapping[str, Any]]:
        async with self.adapters.database.connection() as connection:
            rows = await connection.fetch(statement, *parameters)
        return [dict(row) for row in rows]

    def _extract_for(self, relative: str) -> Any:
        from personal_knowledge.extractors import extract_document
        from personal_knowledge.paths import media_type_for

        media_type = media_type_for(relative)
        assert media_type is not None
        return extract_document((self.root / relative).read_bytes(), media_type)

    # -- migration ----------------------------------------------------------

    async def test_extension_migration_is_applied_and_checksum_guarded(self) -> None:
        index = self.index()
        await index.ensure_ready()
        rows = await self._sql(
            f'SELECT version, checksum FROM "{NAMESPACE}".extension_data_migrations '
            "ORDER BY version"
        )
        self.assertEqual([1], [row["version"] for row in rows])
        self.assertEqual(MIGRATIONS[0]["checksum"], rows[0]["checksum"])
        # The extension schema exists and core tables were not touched.
        schemas = await self._sql(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = $1",
            NAMESPACE,
        )
        self.assertEqual(1, len(schemas))
        with self.assertRaises(DataAccessError) as captured:
            await self.client.migrate(
                [
                    {
                        "version": 1,
                        "path": "migrations/0001_knowledge_index.sql",
                        "checksum": "0" * 64,
                        "description": "tampered",
                    }
                ]
            )
        self.assertEqual("DATA_MIGRATION_INVALID", captured.exception.code)
        await self.client.migrate(list(MIGRATIONS))

    async def test_migration_executes_the_same_bounded_bytes_that_were_hashed(self) -> None:
        payload = self.tmp / "migration-race"
        payload.mkdir()
        migration = payload / "0001_race.sql"
        verified = b"CREATE TABLE verified_probe (value integer);\n"
        swapped = b"CREATE TABLE unverified_probe (value integer);\n"
        migration.write_bytes(verified)
        namespace = f"ext_race_{uuid.uuid4().hex[:12]}"
        client = _BrokerClient(
            self.access,
            ExtensionDataContext(
                extension_id="race.extension",
                extension_version="0.1.0",
                namespace=namespace,
                payload_root=payload,
            ),
        )
        original_open = Path.open
        reads = 0

        def racing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
            nonlocal reads
            mode = str(args[0] if args else kwargs.get("mode", "r"))
            if path == migration and "b" in mode:
                reads += 1
                return io.BytesIO(verified if reads == 1 else swapped)
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", racing_open):
            await client.migrate(
                [
                    {
                        "version": 1,
                        "path": migration.name,
                        "checksum": hashlib.sha256(verified).hexdigest(),
                        "description": "single verified read",
                    }
                ]
            )

        self.assertEqual(1, reads)
        relations = await self._sql(
            "SELECT to_regclass($1) AS verified, to_regclass($2) AS unverified",
            f"{namespace}.verified_probe",
            f"{namespace}.unverified_probe",
        )
        self.assertIsNotNone(relations[0]["verified"])
        self.assertIsNone(relations[0]["unverified"])

    async def test_execute_before_migration_creates_and_stays_in_its_namespace(self) -> None:
        namespace = f"ext_fresh_{uuid.uuid4().hex[:12]}"
        client = _BrokerClient(
            self.access,
            ExtensionDataContext(
                extension_id="fresh.extension",
                extension_version="0.1.0",
                namespace=namespace,
                payload_root=None,
            ),
        )
        await client.execute("CREATE TABLE namespace_probe (value integer NOT NULL)")
        await client.execute("INSERT INTO namespace_probe (value) VALUES ($1)", [7])
        self.assertEqual(
            [{"value": 7}],
            list((await client.execute("SELECT value FROM namespace_probe"))["rows"]),
        )
        located = await self._sql(
            "SELECT table_schema FROM information_schema.tables "
            "WHERE table_name = 'namespace_probe'"
        )
        self.assertEqual([namespace], [row["table_schema"] for row in located])

    async def test_first_call_does_not_deadlock_with_a_single_connection_pool(self) -> None:
        database = PostgresDatabase(
            PostgresAdapterConfig(
                database_url=self.database_url,
                pool_min_size=1,
                pool_max_size=1,
            )
        )
        await database.startup()
        try:
            access = PostgresExtensionDataAccess(database)
            namespace = f"ext_single_{uuid.uuid4().hex[:12]}"
            result = await asyncio.wait_for(
                access.handle(
                    HOST_DATA_EXECUTE,
                    {"statement": "SELECT 1 AS value", "parameters": []},
                    context=ExtensionDataContext(
                        extension_id="single.pool",
                        extension_version="0.1.0",
                        namespace=namespace,
                    ),
                ),
                timeout=3,
            )
            self.assertEqual([{"value": 1}], list(result["rows"]))
        finally:
            await database.close()

    async def test_namespace_lock_wait_is_reported_as_data_timeout(self) -> None:
        namespace = f"ext_locked_{uuid.uuid4().hex[:12]}"
        locker = await asyncpg.connect(self.database_url)
        transaction = locker.transaction()
        await transaction.start()
        try:
            await locker.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
                "pa_extension_data_namespace",
                namespace,
            )
            with self.assertRaises(DataAccessError) as captured:
                await self.access.handle(
                    HOST_DATA_EXECUTE,
                    {
                        "statement": "SELECT 1",
                        "parameters": [],
                        "timeout_seconds": 0.05,
                    },
                    context=ExtensionDataContext(
                        extension_id="locked.extension",
                        extension_version="0.1.0",
                        namespace=namespace,
                    ),
                )
            self.assertEqual("DATA_TIMEOUT", captured.exception.code)
        finally:
            await transaction.rollback()
            await locker.close()

    async def test_migration_cannot_redirect_search_path_to_public(self) -> None:
        payload = self.tmp / "search-path-escape"
        payload.mkdir()
        migration = payload / "0001_escape.sql"
        migration.write_text(
            "SET LOCAL search_path = public;\n"
            "CREATE TABLE namespace_escape_probe (value integer);\n",
            encoding="utf-8",
        )
        namespace = f"ext_escape_{uuid.uuid4().hex[:12]}"
        client = _BrokerClient(
            self.access,
            ExtensionDataContext(
                extension_id="escape.extension",
                extension_version="0.1.0",
                namespace=namespace,
                payload_root=payload,
            ),
        )
        with self.assertRaises(DataAccessError) as captured:
            await client.migrate(
                [
                    {
                        "version": 1,
                        "path": migration.name,
                        "checksum": hashlib.sha256(migration.read_bytes()).hexdigest(),
                        "description": "must remain in its extension namespace",
                    }
                ]
            )
        self.assertEqual("DATA_MIGRATION_INVALID", captured.exception.code)
        leaked = await self._sql("SELECT to_regclass('public.namespace_escape_probe') AS name")
        self.assertIsNone(leaked[0]["name"])

    async def test_late_core_relation_and_statement_timeout_fail_closed(self) -> None:
        await self.client.execute("SELECT 1")
        async with self.adapters.database.connection() as connection:
            await connection.execute("CREATE TABLE public.late_core_probe (value integer)")
        with self.assertRaises(DataAccessError) as captured:
            await self.client.execute("SELECT value FROM late_core_probe")
        self.assertEqual("DATA_STATEMENT_REJECTED", captured.exception.code)

        with self.assertRaises(DataAccessError) as captured:
            await self.client.execute("SELECT pg_sleep(1)", timeout_seconds=0.05)
        self.assertEqual("DATA_TIMEOUT", captured.exception.code)

    # -- build / search -----------------------------------------------------

    async def test_reindex_then_fts_search_returns_locatable_evidence(self) -> None:
        self.write_golden_corpus()
        index = self.index()
        report = await index.reconcile()
        self.assertEqual(len(GOLDEN_FILES) + 1, report.added)
        self.assertEqual(len(GOLDEN_FILES) + 1, report.versions_built)
        self.assertEqual([], list(report.errors))
        outcome = await index.search("halcyon deadline")
        self.assertFalse(outcome.unknown)
        self.assertEqual("disabled", outcome.vector_mode)
        record = outcome.results[0]
        document = self._extract_for(record.relative_path)
        from personal_knowledge.chunking import rebuild_chunk_text

        self.assertEqual(record.text, rebuild_chunk_text(document, record.locator))
        self.assertEqual("research.md", record.relative_path)

    async def test_hybrid_search_uses_both_arms(self) -> None:
        self.write_golden_corpus()
        index = self.index(embedder=DeterministicTestEmbeddingProvider(dim=64))
        await index.reconcile()
        outcome = await index.search("sourdough feeding schedule")
        self.assertEqual("enabled", outcome.vector_mode)
        self.assertFalse(outcome.unknown)
        self.assertEqual("recipe.md", outcome.results[0].relative_path)
        vector_rows = await index.store.search_vector(
            vector=(await index.embedder.embed(["sourdough feeding schedule"]))[0],
            limit=5,
            filters={},
            identity=index.embedder.identity,
        )
        self.assertTrue(vector_rows)
        # FTS matches when the terms occur in one chunk (plainto_tsquery ANDs).
        fts_rows = await index.store.search_fts(
            query="feeding schedule", limit=5, filters={}
        )
        self.assertTrue(fts_rows)
        self.assertEqual("recipe.md", fts_rows[0]["relative_path"])

    async def test_search_returns_stable_order_and_deduplicates(self) -> None:
        self.write_golden_corpus()
        index = self.index(embedder=DeterministicTestEmbeddingProvider(dim=32))
        await index.reconcile()
        first = await index.search("halcyon deadline review", limit=5)
        second = await index.search("halcyon deadline review", limit=5)
        keys = [(row.source_id, row.version_id, row.ordinal) for row in first.results]
        second_keys = [
            (row.source_id, row.version_id, row.ordinal) for row in second.results
        ]
        self.assertEqual(keys, second_keys)
        self.assertEqual(len(keys), len(set(keys)))

    async def test_long_single_line_within_limit_builds_and_remains_locatable(self) -> None:
        self.write("long.txt", "needle " + ("x" * 300_000))
        index = self.index(max_file_bytes=512 * 1024)
        report = await index.reconcile()
        self.assertEqual(1, report.versions_built)
        self.assertEqual((), report.errors)
        outcome = await index.search("needle")
        self.assertFalse(outcome.unknown)
        document = self._extract_for("long.txt")
        from personal_knowledge.chunking import rebuild_chunk_text

        for record in outcome.results:
            self.assertEqual(record.text, rebuild_chunk_text(document, record.locator))

    async def test_search_is_unknown_without_evidence(self) -> None:
        self.write_golden_corpus()
        index = self.index()
        await index.reconcile()
        outcome = await index.search("quasar zyzzyva unlisted")
        self.assertTrue(outcome.unknown)
        self.assertEqual((), outcome.results)

    # -- golden query set ---------------------------------------------------

    async def test_golden_query_set_recall_and_citations(self) -> None:
        self.write_golden_corpus()
        for embedder in (None, DeterministicTestEmbeddingProvider(dim=64)):
            index = self.index(embedder=embedder)
            await index.reconcile()
            hits = 0
            for query, expected in GOLDEN_QUERIES:
                outcome = await index.search(query, limit=5)
                paths = [record.relative_path for record in outcome.results]
                if expected in paths:
                    hits += 1
                evaluated = await index.evaluate(outcome.results, outcome.scores)
                for item in evaluated:
                    record = item.record
                    self.assertEqual("CURRENT", item.status.value)
                    document = self._extract_for(record.relative_path)
                    from personal_knowledge.chunking import rebuild_chunk_text

                    self.assertEqual(
                        record.text,
                        rebuild_chunk_text(document, record.locator),
                        f"locator mismatch for {query!r}",
                    )
                    self.assertEqual(
                        hashlib.sha256(
                            (self.root / record.relative_path).read_bytes()
                        ).hexdigest(),
                        record.source_hash,
                    )
            recall = hits / len(GOLDEN_QUERIES)
            self.assertGreaterEqual(recall, 0.90, f"recall@{5} was {recall:.2f}")

    # -- atomic switching and failure containment ---------------------------

    async def test_build_failure_keeps_old_version_queryable(self) -> None:
        path = self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        index = self.index()
        await index.reconcile()
        before = await index.search("halcyon deadline")
        self.assertFalse(before.unknown)
        old_version = before.results[0].version_id

        path.write_text(
            "# Halcyon\n\nProject Halcyon deadline changed to 2027-01-01.\n",
            encoding="utf-8",
            newline="\n",
        )
        import personal_knowledge.index as index_module

        original = index_module.extract_document

        def explode(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("injected extractor failure")

        index_module.extract_document = explode
        try:
            report = await index.reconcile()
        finally:
            index_module.extract_document = original
        self.assertEqual(0, report.versions_built)
        self.assertGreaterEqual(len(report.errors), 1)
        stored = await self._sql(
            f'SELECT active_version, content_hash FROM "{NAMESPACE}".knowledge_sources'
        )
        self.assertEqual(old_version, stored[0]["active_version"])
        still_queryable = await index.search("halcyon deadline")
        self.assertFalse(still_queryable.unknown)
        self.assertEqual(old_version, still_queryable.results[0].version_id)
        # No half-built rows leak.
        building = await self._sql(
            f'SELECT * FROM "{NAMESPACE}".knowledge_versions WHERE state <> \'READY\''
        )
        self.assertEqual([], building)
        # Recovery: the next reconciliation succeeds and switches atomically.
        report = await index.reconcile()
        self.assertEqual(1, report.versions_built)
        after = await index.search("halcyon deadline")
        self.assertNotEqual(old_version, after.results[0].version_id)

    async def test_building_version_is_invisible_to_queries(self) -> None:
        self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        index = self.index()
        await index.reconcile()
        source_id = (await self._sql(f'SELECT source_id FROM "{NAMESPACE}".knowledge_sources'))[0][
            "source_id"
        ]
        await self.client.transaction(
            [
                {
                    "statement": (
                        f'INSERT INTO "{NAMESPACE}".knowledge_versions ('
                        "source_id, version_id, content_hash, state, extractor_name, "
                        "extractor_version, embedding_provider, embedding_model, "
                        "embedding_dim, embedding_version, chunk_count) "
                        "VALUES ($1, $2, $3, 'BUILDING', 'test', '1', 'none', '', 0, "
                        "'none', 1)"
                    ),
                    "parameters": [source_id, "f" * 64, "e" * 64],
                },
                {
                    "statement": (
                        f'INSERT INTO "{NAMESPACE}".knowledge_chunks ('
                        "source_id, version_id, ordinal, text, locator, content_hash) "
                        "VALUES ($1, $2, 0, 'halfway incomplete quasar', "
                        "'{\"kind\": \"line_range\", \"start\": 1, \"end\": 1, "
                        "\"label\": \"\"}'::jsonb, $3)"
                    ),
                    "parameters": [source_id, "f" * 64, "d" * 64],
                },
            ]
        )
        outcome = await index.search("quasar", limit=5)
        self.assertTrue(outcome.unknown)
        rows = await self._sql(
            f'SELECT source_id FROM "{NAMESPACE}".knowledge_sources '
            "WHERE active_version = $1",
            "f" * 64,
        )
        self.assertEqual([], rows)

    async def test_concurrent_reconciliation_converges(self) -> None:
        self.write_golden_corpus()
        first = self.index(embedder=DeterministicTestEmbeddingProvider(dim=32))
        second = self.index(embedder=DeterministicTestEmbeddingProvider(dim=32))
        await asyncio.gather(first.reconcile(), second.reconcile())
        rows = await self._sql(
            f'SELECT s.source_id, s.active_version, count(v.version_id) AS versions '
            f'FROM "{NAMESPACE}".knowledge_sources s '
            f'LEFT JOIN "{NAMESPACE}".knowledge_versions v ON v.source_id = s.source_id '
            "GROUP BY s.source_id, s.active_version"
        )
        self.assertEqual(len(GOLDEN_FILES) + 1, len(rows))
        for row in rows:
            self.assertIsNotNone(row["active_version"])
            self.assertEqual(1, row["versions"])
        report = await first.reconcile()
        self.assertEqual(0, report.versions_built)
        self.assertEqual(len(GOLDEN_FILES) + 1, report.unchanged)

    # -- deletion propagation ----------------------------------------------

    async def test_delete_removes_content_and_keeps_minimal_tombstone(self) -> None:
        path = self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        index = self.index(embedder=DeterministicTestEmbeddingProvider(dim=16))
        await index.reconcile()
        source_id = (
            await self._sql(f'SELECT source_id FROM "{NAMESPACE}".knowledge_sources')
        )[0]["source_id"]
        path.unlink()
        report = await index.reconcile()
        self.assertEqual(1, report.deleted)
        self.assertTrue(await index.store.search_fts(query="halcyon", limit=5, filters={}) == [])
        self.assertEqual(
            [],
            await self._sql(
                f'SELECT * FROM "{NAMESPACE}".knowledge_chunks WHERE source_id = $1',
                source_id,
            ),
        )
        self.assertEqual(
            [],
            await self._sql(
                f'SELECT * FROM "{NAMESPACE}".knowledge_versions WHERE source_id = $1',
                source_id,
            ),
        )
        tombstones = await self._sql(f'SELECT * FROM "{NAMESPACE}".knowledge_tombstones')
        self.assertEqual(1, len(tombstones))
        self.assertEqual(source_id, tombstones[0]["source_id"])
        self.assertNotIn("text", tombstones[0])
        outcome = await index.search("halcyon", limit=5)
        self.assertTrue(outcome.unknown)

    async def test_stale_citation_after_change_is_not_current(self) -> None:
        path = self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        index = self.index()
        await index.reconcile()
        outcome = await index.search("halcyon deadline")
        evaluated = await index.evaluate(outcome.results, outcome.scores)
        self.assertEqual("CURRENT", evaluated[0].status.value)
        path.write_text(
            "# Halcyon\n\nProject Halcyon deadline is 2027-09-09.\n",
            encoding="utf-8",
            newline="\n",
        )
        evaluated = await index.evaluate(outcome.results, outcome.scores)
        self.assertEqual("STALE", evaluated[0].status.value)
        self.assertEqual("", evaluated[0].text)
        events = await self._sql(
            f'SELECT event_type FROM "{NAMESPACE}".knowledge_events '
            "WHERE event_type = 'knowledge.file_modified'"
        )
        self.assertGreaterEqual(len(events), 1)

    # -- acceptance counterexamples ----------------------------------------

    async def test_transaction_and_migration_cannot_touch_core_tables(self) -> None:
        index = self.index()
        await index.ensure_ready()
        with self.assertRaises(DataAccessError) as captured:
            await self.client.transaction(
                [{"statement": "SELECT id FROM tasks", "parameters": []}]
            )
        self.assertEqual("DATA_STATEMENT_REJECTED", captured.exception.code)
        with self.assertRaises(DataAccessError) as captured:
            await self.client.transaction(
                [{"statement": "ALTER TABLE tasks ADD COLUMN pa_probe text", "parameters": []}]
            )
        self.assertEqual("DATA_STATEMENT_REJECTED", captured.exception.code)

        payload = self.tmp / "hostile_payload"
        (payload / "migrations").mkdir(parents=True)
        sql = "ALTER TABLE tasks ADD COLUMN pa_probe text;\n"
        (payload / "migrations" / "0009_hostile.sql").write_text(sql, encoding="utf-8")
        hostile = _BrokerClient(
            self.access,
            ExtensionDataContext(
                extension_id=EXTENSION_ID,
                extension_version=EXTENSION_VERSION,
                namespace=NAMESPACE,
                payload_root=payload,
            ),
        )
        with self.assertRaises(DataAccessError) as captured:
            await hostile.migrate(
                [
                    {
                        "version": 9,
                        "path": "migrations/0009_hostile.sql",
                        "checksum": hashlib.sha256(sql.encode()).hexdigest(),
                        "description": "hostile",
                    }
                ]
            )
        self.assertEqual("DATA_MIGRATION_INVALID", captured.exception.code)
        columns = await self._sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'tasks' AND column_name = 'pa_probe'"
        )
        self.assertEqual([], columns)
        ledger = await self._sql(
            f'SELECT version FROM "{NAMESPACE}".extension_data_migrations'
        )
        self.assertEqual([1], [row["version"] for row in ledger])

    async def test_nonfinite_and_oversized_results_fail_with_typed_errors(self) -> None:
        with self.assertRaises(DataAccessError) as nonfinite:
            await self.client.execute("SELECT 'NaN'::float8 AS value")
        self.assertEqual("DATA_PROTOCOL_ERROR", nonfinite.exception.code)

        with self.assertRaises(DataAccessError) as oversized:
            await self.client.execute("SELECT repeat('x', 600000) AS value")
        self.assertEqual("DATA_RESULT_TOO_LARGE", oversized.exception.code)

    async def test_removed_root_is_immediately_unsearchable_and_reconciled(self) -> None:
        self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        first = self.index()
        await first.reconcile()
        outcome = await first.search("halcyon deadline")
        self.assertFalse(outcome.unknown)
        archive = self.tmp / "archive"
        archive.mkdir()
        second = self.index(roots=[archive], root_keys=["archive"])
        # The authorization boundary applies before reconciliation runs.
        outcome = await second.search("halcyon deadline")
        self.assertTrue(outcome.unknown)
        report = await second.reconcile()
        self.assertEqual(1, report.deleted)
        self.assertEqual(
            [],
            await self._sql(f'SELECT source_id FROM "{NAMESPACE}".knowledge_sources'),
        )
        self.assertEqual(
            1,
            len(await self._sql(f'SELECT source_id FROM "{NAMESPACE}".knowledge_tombstones')),
        )
        self.assertTrue(await second.store.search_fts(query="halcyon", limit=5, filters={}) == [])

    async def test_renamed_path_can_be_reused_by_a_new_source(self) -> None:
        original = self.write("a.md", "# Original\n\nAlpha identity.\n")
        index = self.index()
        await index.reconcile()
        first_id = (await self._sql(
            f'SELECT source_id FROM "{NAMESPACE}".knowledge_sources '
            "WHERE relative_path = 'a.md'"
        ))[0]["source_id"]
        original.rename(self.root / "b.md")
        await index.reconcile()
        self.write("a.md", "# Replacement\n\nBeta reused path keyword.\n")
        report = await index.reconcile()
        self.assertEqual(1, report.added)
        rows = await self._sql(
            f'SELECT source_id, relative_path FROM "{NAMESPACE}".knowledge_sources '
            "ORDER BY relative_path"
        )
        self.assertEqual(["a.md", "b.md"], [row["relative_path"] for row in rows])
        self.assertNotEqual(rows[0]["source_id"], rows[1]["source_id"])
        self.assertEqual(first_id, rows[1]["source_id"])
        self.assertFalse((await index.search("beta reused path keyword")).unknown)

    async def test_first_build_failure_is_retried_on_the_next_scan(self) -> None:
        path = self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        index = self.index()
        original = index.store.activate_version

        async def refuse(**kwargs: Any) -> None:
            raise RuntimeError("injected activation failure")

        index.store.activate_version = refuse  # type: ignore[method-assign]
        try:
            first = await index.reconcile()
        finally:
            index.store.activate_version = original  # type: ignore[method-assign]
        self.assertEqual(0, first.versions_built)
        rows = await self._sql(
            f'SELECT active_version FROM "{NAMESPACE}".knowledge_sources'
        )
        self.assertEqual(1, len(rows))
        self.assertIsNone(rows[0]["active_version"])
        report = await index.reconcile()
        self.assertEqual(1, report.versions_built)
        outcome = await index.search("halcyon deadline")
        self.assertFalse(outcome.unknown)
        evaluated = await index.evaluate(outcome.results, outcome.scores)
        self.assertEqual("CURRENT", evaluated[0].status.value)
        self.assertTrue(path.exists())

    async def test_cancelled_build_leaves_no_building_rows(self) -> None:
        self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        real_embedder = DeterministicTestEmbeddingProvider(dim=16)

        class SlowEmbedder(DeterministicTestEmbeddingProvider):
            def __init__(self) -> None:
                super().__init__(dim=16)
                self.started = asyncio.Event()

            async def embed(self, texts: Sequence[str]) -> list[list[float]]:
                self.started.set()
                await asyncio.sleep(30)
                return await real_embedder.embed(texts)

        embedder = SlowEmbedder()
        index = self.index(embedder=embedder)
        task = asyncio.create_task(index.reconcile())
        await asyncio.wait_for(embedder.started.wait(), timeout=10)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        rows = await self._sql(
            f"SELECT version_id, state FROM \"{NAMESPACE}\".knowledge_versions"
        )
        self.assertEqual([], [row for row in rows if row["state"] == "BUILDING"])
        chunks = await self._sql(f'SELECT ordinal FROM "{NAMESPACE}".knowledge_chunks')
        self.assertEqual([], chunks)
        # The next reconciliation succeeds normally.
        plain = self.index(embedder=DeterministicTestEmbeddingProvider(dim=16))
        report = await plain.reconcile()
        self.assertEqual(1, report.versions_built)

    async def test_activation_never_destroys_another_build_candidate(self) -> None:
        self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        index = self.index()
        await index.reconcile()
        source_id = (await self._sql(f'SELECT source_id FROM "{NAMESPACE}".knowledge_sources'))[0][
            "source_id"
        ]
        for version_id in ("b" * 64, "c" * 64):
            await index.store.begin_version(
                source_id=source_id,
                version_id=version_id,
                content_hash="1" * 64,
                extractor_name="line-paragraph",
                extractor_version="1.0.0",
                embedding_provider="none",
                embedding_model="",
                embedding_dim=0,
                embedding_version="none",
                built_by="other-run",
            )
        source_snapshot = (await self._sql(
            f'SELECT active_version, root_key, relative_path, generation '
            f'FROM "{NAMESPACE}".knowledge_sources '
            "WHERE source_id = $1",
            source_id,
        ))[0]
        previous_active = source_snapshot["active_version"]
        await index.store.activate_version(
            source_id=source_id,
            version_id="b" * 64,
            content_hash="1" * 64,
            size_bytes=1,
            mtime_ns=1,
            chunk_count=0,
            diagnostics=[],
            expected_active_version=previous_active,
            expected_root_key=source_snapshot["root_key"],
            expected_relative_path=source_snapshot["relative_path"],
            expected_generation=source_snapshot["generation"],
            built_by="other-run",
        )
        remaining = await self._sql(
            f'SELECT version_id, state FROM "{NAMESPACE}".knowledge_versions '
            "WHERE source_id = $1",
            source_id,
        )
        self.assertEqual(
            [("b" * 64, "READY"), ("c" * 64, "BUILDING")],
            sorted(
                (row["version_id"], row["state"]) for row in remaining
            ),
        )
        with self.assertRaises(RuntimeError):
            await index.store.activate_version(
                source_id=source_id,
                version_id="d" * 64,
                content_hash="2" * 64,
                size_bytes=1,
                mtime_ns=1,
                chunk_count=0,
                diagnostics=[],
                expected_active_version="b" * 64,
                expected_root_key=source_snapshot["root_key"],
                expected_relative_path=source_snapshot["relative_path"],
                expected_generation=source_snapshot["generation"] + 1,
                built_by="other-run",
            )
        with self.assertRaises(RuntimeError):
            # A superseded build may not overwrite a newer active version.
            await index.store.activate_version(
                source_id=source_id,
                version_id="c" * 64,
                content_hash="2" * 64,
                size_bytes=1,
                mtime_ns=1,
                chunk_count=0,
                diagnostics=[],
                expected_active_version="z" * 64,
                expected_root_key=source_snapshot["root_key"],
                expected_relative_path=source_snapshot["relative_path"],
                expected_generation=source_snapshot["generation"] + 1,
                built_by="other-run",
            )
        active_after = await self._sql(
            f'SELECT active_version FROM "{NAMESPACE}".knowledge_sources '
            "WHERE source_id = $1",
            source_id,
        )
        self.assertEqual("b" * 64, active_after[0]["active_version"])
        active = await self._sql(
            f'SELECT active_version FROM "{NAMESPACE}".knowledge_sources '
            "WHERE source_id = $1",
            source_id,
        )
        self.assertEqual("b" * 64, active[0]["active_version"])

    async def test_stale_build_and_delete_cannot_mutate_a_moved_source(self) -> None:
        self.write("research.md", "# Halcyon\n\nSnapshot CAS keyword.\n")
        index = self.index()
        await index.reconcile()
        source = (await self._sql(
            f'SELECT source_id, root_key, relative_path, active_version, generation '
            f'FROM "{NAMESPACE}".knowledge_sources'
        ))[0]
        version_id = "f" * 64
        self.assertTrue(
            await index.store.begin_version(
                source_id=source["source_id"],
                version_id=version_id,
                content_hash="2" * 64,
                extractor_name="markdown-line-paragraph",
                extractor_version="1.0.0",
                embedding_provider="none",
                embedding_model="",
                embedding_dim=0,
                embedding_version="none",
                built_by="snapshot-run",
            )
        )
        moved_generation = await index.store.move_source(
            source_id=source["source_id"],
            root_key=source["root_key"],
            relative_path="moved.md",
            media_type="text/markdown",
            size_bytes=1,
            mtime_ns=1,
            expected_root_key=source["root_key"],
            expected_relative_path=source["relative_path"],
            expected_generation=source["generation"],
        )
        self.assertEqual(source["generation"] + 1, moved_generation)
        with self.assertRaises(RuntimeError):
            await index.store.activate_version(
                source_id=source["source_id"],
                version_id=version_id,
                content_hash="2" * 64,
                size_bytes=1,
                mtime_ns=1,
                chunk_count=0,
                diagnostics=[],
                expected_active_version=source["active_version"],
                expected_root_key=source["root_key"],
                expected_relative_path=source["relative_path"],
                expected_generation=source["generation"],
                built_by="snapshot-run",
            )
        deleted = await index.store.delete_source(
            source_id=source["source_id"],
            reason_hash="2" * 64,
            expected_root_key=source["root_key"],
            expected_relative_path=source["relative_path"],
            expected_generation=source["generation"],
        )
        self.assertIsNone(deleted)
        current = await self._sql(
            f'SELECT relative_path, generation FROM "{NAMESPACE}".knowledge_sources '
            "WHERE source_id = $1",
            source["source_id"],
        )
        self.assertEqual([("moved.md", moved_generation)], [tuple(row.values()) for row in current])

    async def test_build_heartbeat_prevents_live_candidate_reaping(self) -> None:
        self.write("research.md", "# Halcyon\n\nHeartbeat keyword.\n")
        index = self.index()
        await index.reconcile()
        source_id = (await self._sql(
            f'SELECT source_id FROM "{NAMESPACE}".knowledge_sources'
        ))[0]["source_id"]
        version_id = "e" * 64
        self.assertTrue(
            await index.store.begin_version(
                source_id=source_id,
                version_id=version_id,
                content_hash="3" * 64,
                extractor_name="markdown-line-paragraph",
                extractor_version="1.0.0",
                embedding_provider="none",
                embedding_model="",
                embedding_dim=0,
                embedding_version="none",
                built_by="live-run",
            )
        )
        await self.client.execute(
            "UPDATE knowledge_versions SET heartbeat_at = now() - interval '3 hours' "
            "WHERE source_id = $1 AND version_id = $2",
            [source_id, version_id],
        )
        self.assertTrue(
            await index.store.heartbeat_version(
                source_id=source_id, version_id=version_id, built_by="live-run"
            )
        )
        await index.store.discard_orphan_building_versions(
            run_id="other-run", max_age_seconds=3600
        )
        remaining = await self._sql(
            f'SELECT version_id FROM "{NAMESPACE}".knowledge_versions '
            "WHERE source_id = $1 AND version_id = $2",
            source_id,
            version_id,
        )
        self.assertEqual(1, len(remaining))

    async def test_same_version_cannot_be_reclaimed_or_cleaned_by_another_run(self) -> None:
        self.write("research.md", "# Halcyon\n\nConcurrent ownership keyword.\n")
        index = self.index()
        await index.reconcile()
        row = (await self._sql(
            f'SELECT source_id, active_version FROM "{NAMESPACE}".knowledge_sources'
        ))[0]
        source_id = row["source_id"]
        version_id = row["active_version"]
        claimed = await index.store.begin_version(
            source_id=source_id,
            version_id=version_id,
            content_hash="1" * 64,
            extractor_name="line-paragraph",
            extractor_version="1.0.0",
            embedding_provider="none",
            embedding_model="",
            embedding_dim=0,
            embedding_version="none",
            built_by="competing-run",
        )
        self.assertFalse(claimed)
        await index.store.fail_version(
            source_id=source_id,
            version_id=version_id,
            built_by="competing-run",
        )
        chunks = await self._sql(
            f'SELECT count(*) AS total FROM "{NAMESPACE}".knowledge_chunks '
            "WHERE source_id = $1 AND version_id = $2",
            source_id,
            version_id,
        )
        self.assertGreater(chunks[0]["total"], 0)
        self.assertFalse((await index.search("concurrent ownership keyword")).unknown)

    async def test_orphan_building_version_is_cleaned_up(self) -> None:
        self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        index = self.index()
        await index.reconcile()
        source_id = (await self._sql(f'SELECT source_id FROM "{NAMESPACE}".knowledge_sources'))[0][
            "source_id"
        ]
        await self.client.transaction(
            [
                {
                    "statement": (
                        f'INSERT INTO "{NAMESPACE}".knowledge_versions ('
                        "source_id, version_id, content_hash, state, extractor_name, "
                        "extractor_version, embedding_provider, embedding_model, "
                        "embedding_dim, embedding_version, chunk_count, built_by, "
                        "created_at, heartbeat_at) "
                        "VALUES ($1, $2, $3, 'BUILDING', 'line-paragraph', '1.0.0', "
                        "'none', '', 0, 'none', 0, 'dead-run', "
                        "now() - interval '3 hours', now() - interval '3 hours')"
                    ),
                    "parameters": [source_id, "e" * 64, "1" * 64],
                }
            ]
        )
        report = await index.reconcile()
        self.assertEqual(0, report.versions_built)
        rows = await self._sql(
            f'SELECT version_id FROM "{NAMESPACE}".knowledge_versions '
            "WHERE state = 'BUILDING'"
        )
        self.assertEqual([], rows)

    async def test_embedding_identity_change_rebuilds_versions(self) -> None:
        self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        first = self.index(embedder=DeterministicTestEmbeddingProvider(dim=16, model="model-a"))
        await first.reconcile()
        before = await self._sql(
            f'SELECT active_version FROM "{NAMESPACE}".knowledge_sources'
        )
        second = self.index(embedder=DeterministicTestEmbeddingProvider(dim=8, model="model-b"))
        report = await second.reconcile()
        self.assertEqual(1, report.versions_built)
        after = await self._sql(
            f'SELECT active_version FROM "{NAMESPACE}".knowledge_sources'
        )
        self.assertNotEqual(before[0]["active_version"], after[0]["active_version"])
        version = await self._sql(
            f'SELECT embedding_provider, embedding_model, embedding_dim '
            f'FROM "{NAMESPACE}".knowledge_versions WHERE version_id = $1',
            after[0]["active_version"],
        )
        self.assertEqual(
            [("test.fake", "model-b", 8)],
            [
                (
                    row["embedding_provider"],
                    row["embedding_model"],
                    row["embedding_dim"],
                )
                for row in version
            ],
        )
        again = await second.reconcile()
        self.assertEqual(1, again.unchanged)
        self.assertEqual(0, again.versions_built)

    async def test_changed_embedding_identity_never_queries_old_vector_space(self) -> None:
        self.write("research.md", "# Halcyon\n\nIdentity gate keyword.\n")
        first = self.index(
            embedder=DeterministicTestEmbeddingProvider(dim=16, model="model-a")
        )
        await first.reconcile()
        for dim in (16, 8):
            changed = self.index(
                embedder=DeterministicTestEmbeddingProvider(dim=dim, model="model-b")
            )
            outcome = await changed.search("identity gate keyword")
            self.assertFalse(outcome.unknown)
            self.assertEqual("disabled", outcome.vector_mode)

    async def test_repeated_hash_transition_is_not_permanently_deduplicated(self) -> None:
        path = self.write("state.md", "# State\n\nValue A.\n")
        index = self.index()
        self.assertEqual(1, (await index.reconcile()).events)
        counts: list[int] = []
        for body in (
            "# State\n\nValue B.\n",
            "# State\n\nValue A.\n",
            "# State\n\nValue B.\n",
        ):
            path.write_text(body, encoding="utf-8", newline="\n")
            counts.append((await index.reconcile()).events)
        self.assertEqual([1, 1, 1], counts)
        events = await self._sql(
            f'SELECT event_type FROM "{NAMESPACE}".knowledge_events ORDER BY sequence'
        )
        self.assertEqual(4, len(events))

    async def test_stale_and_deleted_results_never_expose_their_body(self) -> None:
        path = self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        extension = PersonalKnowledgeExtension()
        runtime = await self._runtime()
        await extension.initialize(runtime)
        await extension.invoke("knowledge.reindex", {}, context=_invocation_context())
        search = await extension.invoke(
            "knowledge.search",
            {"query": "halcyon deadline", "limit": 5},
            context=_invocation_context(),
        )
        self.assertFalse(search.output["unknown"])
        path.write_text(
            "# Halcyon\n\nProject Halcyon deadline changed to 2027-01-01.\n",
            encoding="utf-8",
            newline="\n",
        )
        stale = await extension.invoke(
            "knowledge.search",
            {"query": "halcyon deadline", "limit": 5},
            context=_invocation_context(),
        )
        self.assertTrue(stale.output["unknown"])
        for item in stale.output["results"]:
            self.assertEqual("STALE", item["status"])
            self.assertEqual("", item["text"])
        from personal_assistant_sdk import ContextQuery

        evidence = await extension.retrieve(ContextQuery(text="halcyon deadline", limit=5))
        self.assertEqual((), evidence)

    # -- path safety and source immutability --------------------------------
    async def test_out_of_root_symlink_is_never_indexed(self) -> None:
        (self.outside / "secret.md").write_text(
            "# Secret\n\nclassified quasar material\n", encoding="utf-8", newline="\n"
        )
        link = self.root / "escape.md"
        try:
            link.symlink_to(self.outside / "secret.md")
        except OSError as exc:
            self.skipTest(f"cannot create a symlink here: {exc}")
        index = self.index()
        report = await index.reconcile()
        self.assertEqual(0, report.added)
        outcome = await index.search("classified quasar")
        self.assertTrue(outcome.unknown)

    async def test_indexing_never_modifies_source_files(self) -> None:
        self.write_golden_corpus()
        before = {
            path.relative_to(self.root).as_posix(): (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
            for path in self.root.rglob("*")
            if path.is_file()
        }
        index = self.index(embedder=DeterministicTestEmbeddingProvider(dim=16))
        await index.reconcile()
        await index.reconcile()
        after = {
            path.relative_to(self.root).as_posix(): (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    # -- worker surface -----------------------------------------------------

    async def test_worker_search_output_has_complete_citations(self) -> None:
        self.write_golden_corpus()
        extension = PersonalKnowledgeExtension()
        runtime = await self._runtime()
        await extension.initialize(runtime)
        result = await extension.invoke(
            "knowledge.reindex",
            {"mode": "incremental"},
            context=_invocation_context(),
        )
        self.assertEqual("SUCCEEDED", result.outcome.value)
        self.assertEqual(len(GOLDEN_FILES) + 1, result.output["versions_built"])
        search = await extension.invoke(
            "knowledge.search",
            {"query": "halcyon deadline", "limit": 5},
            context=_invocation_context(),
        )
        self.assertEqual("SUCCEEDED", search.outcome.value)
        self.assertFalse(search.output["unknown"])
        first = search.output["results"][0]
        for key in (
            "text",
            "source_uri",
            "source_version",
            "content_hash",
            "locator",
            "extension_id",
            "extension_version",
            "sensitivity",
            "trust",
            "status",
        ):
            self.assertIn(key, first)
        self.assertTrue(str(first["source_uri"]).startswith("knowledge://"))
        self.assertEqual("CURRENT", first["status"])
        locator = first["locator"]
        document = self._extract_for(str(first["source_uri"]).split("/", 3)[-1].replace("%20", " "))
        from personal_knowledge.chunking import rebuild_chunk_text
        from personal_knowledge.store import parse_locator

        self.assertEqual(
            first["text"], rebuild_chunk_text(document, parse_locator(locator))
        )

    async def test_worker_retrieve_marks_deleted_sources(self) -> None:
        path = self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        extension = PersonalKnowledgeExtension()
        await extension.initialize(await self._runtime())
        await extension.invoke("knowledge.reindex", {}, context=_invocation_context())
        from personal_assistant_sdk import ContextQuery

        evidence = await extension.retrieve(ContextQuery(text="halcyon deadline", limit=5))
        self.assertEqual(1, len(evidence))
        self.assertEqual("CURRENT", evidence[0].metadata["status"])
        path.unlink()
        # Deleted sources never enter the model context, not even as metadata.
        evidence = await extension.retrieve(ContextQuery(text="halcyon deadline", limit=5))
        self.assertEqual((), evidence)

    async def test_worker_health_reports_missing_configuration_without_failing(self) -> None:
        from personal_assistant_sdk import RuntimeContext

        extension = PersonalKnowledgeExtension()
        await extension.initialize(
            RuntimeContext(
                protocol_version="1",
                extension_id=EXTENSION_ID,
                extension_version=EXTENSION_VERSION,
                data_namespace=NAMESPACE,
                host_data=self.client,
            )
        )
        report = await extension.health()
        self.assertTrue(report.healthy)
        self.assertEqual("awaiting_configuration", report.status)

    async def test_worker_health_rejects_semantically_invalid_embedding_config(self) -> None:
        extension = PersonalKnowledgeExtension()
        await extension.initialize(
            await self._runtime(embedding={"provider": "ollama"})
        )
        report = await extension.health()
        self.assertFalse(report.healthy)
        self.assertEqual("config_invalid", report.status)
        self.assertEqual("EMBEDDING_CONFIG_INVALID", report.details["code"])

    async def test_worker_migrations_and_forms_match_manifest(self) -> None:
        extension = PersonalKnowledgeExtension()
        await extension.initialize(await self._runtime())
        descriptors = extension.migrations()
        self.assertEqual([1], [item.version for item in descriptors])
        self.assertEqual(
            MIGRATIONS[0]["checksum"], descriptors[0].checksum
        )
        self.assertEqual("migrations/0001_knowledge_index.sql", descriptors[0].path)
        forms = extension.forms()
        self.assertEqual(["knowledge.roots"], [item.id for item in forms])
        self.assertIn("roots", forms[0].json_schema["properties"])
        tools = extension.tools()
        risks = {tool.id: tool.risk.value for tool in tools}
        self.assertEqual(
            {"knowledge.search": "READ", "knowledge.reindex": "INTERNAL_WRITE"}, risks
        )

    async def test_worker_event_source_reports_changes(self) -> None:
        self.write("research.md", "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n")
        extension = PersonalKnowledgeExtension()
        await extension.initialize(await self._runtime())
        from personal_assistant_sdk import PollRequest

        first = await extension.poll(
            PollRequest(
                source_id="knowledge.file_changes",
                cursor=None,
                deadline="2099-01-01T00:00:00Z",
            )
        )
        self.assertEqual(1, len(first.events))
        self.assertEqual("knowledge.file_added", first.events[0].type)
        second = await extension.poll(
            PollRequest(
                source_id="knowledge.file_changes",
                cursor=first.next_cursor,
                deadline="2099-01-01T00:00:00Z",
            )
        )
        self.assertEqual(0, len(second.events))
        self.write("extra.md", "# Extra\n\nNew file body.\n")
        third = await extension.poll(
            PollRequest(
                source_id="knowledge.file_changes",
                cursor=second.next_cursor,
                deadline="2099-01-01T00:00:00Z",
            )
        )
        self.assertEqual(1, len(third.events))
        self.assertEqual("knowledge.file_added", third.events[0].type)


def _invocation_context() -> Any:
    from personal_assistant_sdk import InvocationContext

    return InvocationContext(
        task_id="task-f05",
        run_id="run-f05",
        deadline="2099-01-01T00:00:00Z",
        idempotency_key="f05-test",
    )


if __name__ == "__main__":
    unittest.main()
