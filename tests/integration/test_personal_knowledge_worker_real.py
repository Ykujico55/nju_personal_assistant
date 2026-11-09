"""F05: real Extension Worker end-to-end with the real PostgreSQL data broker.

Covers install -> enable -> reindex -> retrieve -> disable -> recover -> uninstall
with the real per-version venv, real worker subprocess and the real PostgreSQL +
pgvector index behind the generic host data capability.  Requires
``PA_TEST_DATABASE_URL`` pointing at a dedicated ``*_test`` database.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
import tempfile
import unittest
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from personal_assistant.core.extensions.config import ExtensionConfigStore  # noqa: F401
from personal_assistant.core.extensions.lifecycle import (
    InstallationConfirmation,
    InstallCoordinator,
    LifecycleManager,
)
from personal_assistant.core.extensions.registry import ExtensionRegistry
from personal_assistant.core.extensions.supervision import ExtensionSupervisorService
from personal_assistant.infrastructure.database import (
    PostgresAdapterConfig,
    PostgresAdapters,
    PostgresExtensionDataAccess,
    build_postgres_adapters,
)
from personal_assistant.infrastructure.database.extension_versions import (
    PostgresVersionCatalog,
)
from personal_assistant.infrastructure.database.operations import (
    PostgresExtensionOperationStore,
)
from personal_assistant.infrastructure.extensions import (
    CompatibleVersionOperator,
    FileExtensionConfigStore,
    LocalArtifactStager,
    PostgresExtensionDataStore,
    ProcessContractVerifier,
    ProcessRuntimeSupervisor,
    VenvArtifactInstaller,
)

BASE_URL = os.getenv("PA_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = ROOT / "extensions" / "personal_knowledge"
EXTENSION_ID = "personal.knowledge"
NAMESPACE = "ext_personal_2e_knowledge"


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


@unittest.skipUnless(BASE_URL, "set PA_TEST_DATABASE_URL to run real PostgreSQL tests")
class PersonalKnowledgeWorkerRealTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = _dsn(BASE_URL or "")
        if not urlsplit(base).path.lstrip("/").endswith("_test"):
            raise RuntimeError("PA_TEST_DATABASE_URL must name a dedicated *_test database")
        self._admin_dsn = _with_db(base, "postgres")
        self._db_name = f"pa_f05_worker_{uuid.uuid4().hex}"
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{self._db_name}"')
        finally:
            await admin.close()
        self.database_url = _with_db(base, self._db_name)
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f05_worker_"))
        self.notes = self.tmp / "notes"
        self.notes.mkdir()
        (self.notes / "research.md").write_text(
            "# Halcyon\n\nProject Halcyon deadline is 2026-08-30.\n",
            encoding="utf-8",
            newline="\n",
        )
        self.adapters: PostgresAdapters = build_postgres_adapters(
            PostgresAdapterConfig(database_url=self.database_url)
        )
        await self.adapters.startup()
        self.config_store = FileExtensionConfigStore(self.tmp / "config")
        await self.config_store.save(
            EXTENSION_ID,
            {
                "roots": [{"path": str(self.notes), "key": "notes"}],
                "embedding": {"provider": "none"},
            },
        )
        self.stager = LocalArtifactStager(self.tmp / "staging")
        self.installer = VenvArtifactInstaller(
            install_root=self.tmp / "installed",
            stager=self.stager,
            python_executable=sys.executable,
        )
        self.registry = ExtensionRegistry()
        self.runtime = ProcessRuntimeSupervisor(
            handshake_timeout_seconds=30.0,
            health_timeout_seconds=15.0,
            data_access=PostgresExtensionDataAccess(self.adapters.database),
            config_store=self.config_store,
        )
        coordinator = InstallCoordinator(
            self.stager,
            self.installer,
            ProcessContractVerifier(handshake_timeout_seconds=30.0),
            self.adapters.lifecycle_store,
        )
        manager = LifecycleManager(
            self.adapters.lifecycle_store,
            self.registry,
            self.runtime,
            self.installer,
            PostgresExtensionDataStore(self.adapters.database),
            CompatibleVersionOperator(PostgresVersionCatalog(self.adapters.database)),
        )
        self.service = ExtensionSupervisorService(
            coordinator=coordinator,
            manager=manager,
            registry=self.registry,
            store=self.adapters.lifecycle_store,
            operations=PostgresExtensionOperationStore(self.adapters.database),
            runtime=self.runtime,
        )

    async def asyncTearDown(self) -> None:
        with contextlib.suppress(Exception):
            await self.runtime.stop_all()
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
        _safe_rmtree(self.tmp)

    def _confirmation(self, preview: object) -> InstallationConfirmation:
        return InstallationConfirmation(
            plan_id=preview.plan_id,  # type: ignore[attr-defined]
            confirmation_nonce=preview.confirmation_nonce,  # type: ignore[attr-defined]
            preview_hash=preview.preview_hash,  # type: ignore[attr-defined]
            actor="f05-integration",
            confirmed_at=datetime.now(UTC),
            accepted_warning=True,
        )

    async def _run(self, operation: object) -> object:
        return await self.service.wait_operation(
            operation.id, timeout_seconds=300.0  # type: ignore[attr-defined]
        )

    async def _sql(self, statement: str, *parameters: object) -> list[dict]:
        async with self.adapters.database.connection() as connection:
            rows = await connection.fetch(statement, *parameters)
        return [dict(row) for row in rows]

    def _zip_artifact(self, target: Path) -> Path:
        skipped = {".venv", "__pycache__", ".pytest_cache", ".mypy_cache"}
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(EXTENSION_DIR.rglob("*")):
                relative = path.relative_to(EXTENSION_DIR)
                if any(part in skipped for part in relative.parts):
                    continue
                if path.is_file():
                    archive.write(path, relative.as_posix())
        return target

    async def test_shared_workspace_applies_and_indexes_without_reindex_command(self) -> None:
        """Real Worker indexes applied roots automatically and skips READY on restart."""
        from personal_assistant.infrastructure.extensions.workspace import (
            WorkspaceExtensionConfigStore,
        )

        workspace = WorkspaceExtensionConfigStore(self.config_store, self.tmp / "workspace")
        await workspace.save(
            EXTENSION_ID,
            {
                "workspace_binding": "instance",
                "embedding": {"provider": "none"},
            },
        )
        config_state = await workspace.status(EXTENSION_ID)
        await workspace.apply(EXTENSION_ID, str(config_state["saved_digest"]))

        def configuration(path: Path) -> dict:
            return {
                "workspace_id": "instance",
                "roots": [
                    {
                        "key": "notes",
                        "label": "Synthetic notes",
                        "path": str(path),
                        "mode": "read_write",
                    }
                ],
            }

        saved_a = await workspace.save_workspace(configuration(self.notes))
        await workspace.apply_workspace(saved_a["application"]["saved_digest"])
        self.runtime = ProcessRuntimeSupervisor(
            handshake_timeout_seconds=30.0,
            health_timeout_seconds=15.0,
            data_access=PostgresExtensionDataAccess(self.adapters.database),
            config_store=workspace,
        )
        coordinator = InstallCoordinator(
            self.stager,
            self.installer,
            ProcessContractVerifier(handshake_timeout_seconds=30.0),
            self.adapters.lifecycle_store,
        )
        manager = LifecycleManager(
            self.adapters.lifecycle_store,
            self.registry,
            self.runtime,
            self.installer,
            PostgresExtensionDataStore(self.adapters.database),
            CompatibleVersionOperator(PostgresVersionCatalog(self.adapters.database)),
        )
        self.service = ExtensionSupervisorService(
            coordinator=coordinator,
            manager=manager,
            registry=self.registry,
            store=self.adapters.lifecycle_store,
            operations=PostgresExtensionOperationStore(self.adapters.database),
            runtime=self.runtime,
        )

        async def binding() -> dict | None:
            table = await self._sql(
                "SELECT to_regclass($1) AS relation", f"{NAMESPACE}.knowledge_meta"
            )
            if table[0]["relation"] is None:
                return None
            rows = await self._sql(
                "SELECT value->>'state' AS state, value->>'binding_hash' AS binding_hash, "
                f"value->>'scan_id' AS scan_id FROM \"{NAMESPACE}\".knowledge_meta "
                "WHERE key='index.binding.v1'"
            )
            return rows[0] if rows else None

        async def ready(previous: dict | None = None) -> dict:
            async with asyncio.timeout(30):
                while True:
                    worker = self.runtime._workers[EXTENSION_ID]  # noqa: SLF001
                    self.assertTrue(worker.client.running, "automatic indexing lost its worker")
                    health = await worker.health(timeout_seconds=10.0)
                    self.assertNotEqual(
                        "FAILED", health.get("details", {}).get("index_state"), health
                    )
                    current = await binding()
                    if (
                        current
                        and current["state"] == "READY"
                        and (
                            previous is None or current["binding_hash"] != previous["binding_hash"]
                        )
                        and health.get("details", {}).get("index_state") != "INDEXING"
                    ):
                        return current
                    await asyncio.sleep(0.1)

        async def search(query: str) -> dict:
            return await self.service.invoke_tool(
                EXTENSION_ID,
                "knowledge.search",
                {"query": query, "limit": 5},
                task_id="workspace-auto-test",
                run_id="workspace-auto-run",
                idempotency_key="search-" + uuid.uuid4().hex,
                deadline_seconds=30.0,
            )

        async def settled_operation(operation: object) -> object:
            result = await self._run(operation)
            # A durable terminal receipt can precede releasing the lifecycle lock.
            # Join this fixture's accepted command before starting the next one.
            tasks = tuple(self.service._tasks)  # noqa: SLF001 - test-owned runner tasks
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=30.0)
                for task in done:
                    task.result()
                self.assertFalse(pending, "lifecycle runner did not finish cleanup")
            self.assertNotIn(EXTENSION_ID, self.service._active)  # noqa: SLF001
            return result

        workers = []
        try:
            preview = await self.service.inspect(str(EXTENSION_DIR))
            installed = await settled_operation(
                await self.service.begin_install(
                    preview.plan_id,
                    self._confirmation(preview),
                )
            )
            self.assertEqual("SUCCEEDED", installed.status.value, installed.diagnostic_code)
            enabled = await settled_operation(await self.service.begin_enable(EXTENSION_ID))
            self.assertEqual("SUCCEEDED", enabled.status.value, enabled.diagnostic_code)
            workers.append(self.runtime._workers[EXTENSION_ID])  # noqa: SLF001
            first = await ready()
            first_results = (await search("halcyon deadline"))["output"]["results"]
            self.assertEqual(1, len(first_results))
            self.assertEqual("knowledge://notes/research.md", first_results[0]["source_uri"])

            other = self.tmp / "other-notes"
            other.mkdir()
            (other / "replacement.md").write_text(
                "# Zephyr\n\nZephyr replacement deadline is 2030-01-01.\n", encoding="utf-8"
            )
            saved_b = await workspace.save_workspace(configuration(other))
            await self.runtime.synchronize_configurations()
            self.assertIs(workers[0], self.runtime._workers[EXTENSION_ID])  # noqa: SLF001
            self.assertEqual(
                first, await binding(), "saving alone must not rebuild or switch roots"
            )
            self.assertEqual(1, len((await search("halcyon deadline"))["output"]["results"]))

            await workspace.apply_workspace(saved_b["application"]["saved_digest"])
            await self.runtime.synchronize_configurations()
            workers.append(self.runtime._workers[EXTENSION_ID])  # noqa: SLF001
            self.assertIsNot(workers[0], workers[1])
            self.assertFalse(workers[0].client.running, "old-root worker must close on apply")
            second = await ready(first)
            self.assertNotEqual(first["scan_id"], second["scan_id"])
            second_results = (await search("zephyr replacement"))["output"]["results"]
            self.assertEqual(1, len(second_results))
            self.assertEqual("knowledge://notes/replacement.md", second_results[0]["source_uri"])
            self.assertEqual([], (await search("halcyon"))["output"]["results"])

            await self.runtime.stop_all()
            self.assertFalse(workers[1].client.running)
            await self.service.recover()
            workers.append(self.runtime._workers[EXTENSION_ID])  # noqa: SLF001
            restored = await ready()
            self.assertEqual(
                second["scan_id"], restored["scan_id"], "READY restart must not rescan"
            )
            self.assertEqual(1, len((await search("zephyr replacement"))["output"]["results"]))
        finally:
            await self.service.stop_all()
            self.assertFalse(self.runtime._workers)  # noqa: SLF001
            self.assertTrue(all(not worker.client.running for worker in workers))

    async def test_zip_artifact_installs_and_serves(self) -> None:
        """The extension ships as an independent artifact (zip of its root)."""

        artifact = self._zip_artifact(self.tmp / "personal-knowledge-0.1.0.zip")
        preview = await self.service.inspect(str(artifact))
        self.assertEqual(EXTENSION_ID, preview.extension_id)
        self.assertEqual("zip", "zip" if artifact.suffix == ".zip" else preview.mode)
        operation = await self.service.begin_install(
            preview.plan_id, self._confirmation(preview)
        )
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
        operation = await self.service.begin_enable(EXTENSION_ID)
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
        try:
            reindexed = await self.service.invoke_tool(
                EXTENSION_ID,
                "knowledge.reindex",
                {"mode": "incremental"},
                task_id="task-f05-zip",
                run_id="run-f05-zip",
                idempotency_key="f05-zip-reindex",
                deadline_seconds=120.0,
            )
            self.assertEqual(1, reindexed["output"]["versions_built"])
            search = await self.service.invoke_tool(
                EXTENSION_ID,
                "knowledge.search",
                {"query": "halcyon deadline", "limit": 5},
                task_id="task-f05-zip",
                run_id="run-f05-zip",
                idempotency_key="f05-zip-search",
                deadline_seconds=60.0,
            )
            self.assertEqual(1, len(search["output"]["results"]))
            self.assertEqual("CURRENT", search["output"]["results"][0]["status"])
        finally:
            operation = await self.service.begin_uninstall(EXTENSION_ID)
            result = await self._run(operation)
            self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)

    async def test_install_enable_reindex_retrieve_disable_recover_uninstall(self) -> None:
        preview = await self.service.inspect(str(EXTENSION_DIR))
        self.assertEqual(EXTENSION_ID, preview.extension_id)
        self.assertEqual(
            {"knowledge.search": "READ", "knowledge.reindex": "INTERNAL_WRITE"},
            dict(preview.tool_risks),
        )
        operation = await self.service.begin_install(
            preview.plan_id, self._confirmation(preview)
        )
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)

        operation = await self.service.begin_enable(EXTENSION_ID)
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)

        # The worker subprocess has a live environment without database credentials.
        worker = self.runtime._workers[EXTENSION_ID]  # noqa: SLF001 - real process handle
        self.assertTrue(worker.client.running)

        tool_result = await self.service.invoke_tool(
            EXTENSION_ID,
            "knowledge.reindex",
            {"mode": "incremental"},
            task_id="task-f05",
            run_id="run-f05",
            idempotency_key="f05-reindex-1",
            deadline_seconds=120.0,
        )
        self.assertEqual("SUCCEEDED", tool_result["outcome"])
        self.assertEqual(1, tool_result["output"]["versions_built"])

        migrations = await self._sql(
            f'SELECT version, checksum FROM "{NAMESPACE}".extension_data_migrations'
        )
        self.assertEqual([1], [row["version"] for row in migrations])

        search = await self.service.invoke_tool(
            EXTENSION_ID,
            "knowledge.search",
            {"query": "halcyon deadline", "limit": 5},
            task_id="task-f05",
            run_id="run-f05",
            idempotency_key="f05-search-1",
            deadline_seconds=60.0,
        )
        self.assertEqual("SUCCEEDED", search["outcome"])
        results = search["output"]["results"]
        self.assertEqual(1, len(results))
        first = results[0]
        self.assertEqual("CURRENT", first["status"])
        self.assertTrue(first["source_uri"].startswith("knowledge://notes/"))
        self.assertEqual(64, len(first["content_hash"]))
        self.assertIn("locator", first)

        context = await worker.call_slot(
            "context.retrieve",
            {"query": {"text": "halcyon deadline", "limit": 5}},
            timeout_seconds=60.0,
        )
        self.assertEqual(1, len(context))
        self.assertEqual("knowledge://notes/research.md", context[0]["source_uri"])
        self.assertEqual(64, len(context[0]["content_hash"]))
        metadata = context[0]["metadata"]
        for key in (
            "source_uri",
            "source_version",
            "content_hash",
            "locator",
            "sensitivity",
            "trust",
            "extension_id",
            "extension_version",
        ):
            self.assertIn(key, metadata)
        self.assertEqual("CURRENT", metadata["status"])
        self.assertEqual(context[0]["content_hash"], metadata["content_hash"])

        # Host restart: persisted ENABLED state restarts the real worker.
        await self.runtime.stop_all()
        self.assertFalse(self.runtime._workers)  # noqa: SLF001
        await self.service.recover()
        self.assertTrue(self.runtime._workers[EXTENSION_ID].client.running)  # noqa: SLF001
        search_after_restart = await self.service.invoke_tool(
            EXTENSION_ID,
            "knowledge.search",
            {"query": "halcyon deadline", "limit": 5},
            task_id="task-f05",
            run_id="run-f05",
            idempotency_key="f05-search-2",
            deadline_seconds=60.0,
        )
        self.assertEqual(1, len(search_after_restart["output"]["results"]))

        operation = await self.service.begin_disable(EXTENSION_ID)
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
        self.assertIsNone(self.registry.snapshot.owner_of("knowledge.search"))
        self.assertFalse(self.runtime._workers)  # noqa: SLF001
        record = await self.service.record(EXTENSION_ID)
        self.assertEqual("DISABLED", record.state.value)  # type: ignore[union-attr]

        operation = await self.service.begin_uninstall(EXTENSION_ID)
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
        record = await self.service.record(EXTENSION_ID)
        self.assertEqual("UNINSTALLED", record.state.value)  # type: ignore[union-attr]
        self.assertTrue(record.data_retained)  # type: ignore[union-attr]
        namespaces = await self._sql(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = $1",
            NAMESPACE,
        )
        self.assertEqual(1, len(namespaces), "business data must be retained")

    async def test_worker_without_configuration_fails_closed_without_crashing(self) -> None:
        from personal_assistant.core.extensions.errors import RpcCallError

        empty_store = FileExtensionConfigStore(self.tmp / "empty_config")
        runtime = ProcessRuntimeSupervisor(
            handshake_timeout_seconds=30.0,
            health_timeout_seconds=15.0,
            data_access=PostgresExtensionDataAccess(self.adapters.database),
            config_store=empty_store,
        )
        coordinator = InstallCoordinator(
            self.stager,
            self.installer,
            ProcessContractVerifier(handshake_timeout_seconds=30.0),
            self.adapters.lifecycle_store,
        )
        manager = LifecycleManager(
            self.adapters.lifecycle_store,
            self.registry,
            runtime,
            self.installer,
            PostgresExtensionDataStore(self.adapters.database),
            CompatibleVersionOperator(PostgresVersionCatalog(self.adapters.database)),
        )
        service = ExtensionSupervisorService(
            coordinator=coordinator,
            manager=manager,
            registry=self.registry,
            store=self.adapters.lifecycle_store,
            operations=PostgresExtensionOperationStore(self.adapters.database),
            runtime=runtime,
        )
        try:
            preview = await service.inspect(str(EXTENSION_DIR))
            operation = await service.begin_install(
                preview.plan_id, self._confirmation(preview)
            )
            result = await service.wait_operation(operation.id, timeout_seconds=300.0)
            self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
            operation = await service.begin_enable(EXTENSION_ID)
            result = await service.wait_operation(operation.id, timeout_seconds=300.0)
            self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
            with self.assertRaises(RpcCallError) as captured:
                await service.invoke_tool(
                    EXTENSION_ID,
                    "knowledge.reindex",
                    {},
                    task_id="task-f05",
                    run_id="run-f05",
                    idempotency_key="f05-reindex-unconfigured",
                    deadline_seconds=60.0,
                )
            self.assertEqual(-32602, captured.exception.code)
            self.assertTrue(
                runtime._workers[EXTENSION_ID].client.running,  # noqa: SLF001
                "a typed configuration error must not kill the worker",
            )
        finally:
            await runtime.stop_all()


if __name__ == "__main__":
    unittest.main()
