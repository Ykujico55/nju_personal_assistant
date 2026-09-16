"""F02 real-PostgreSQL persistence tests for supervisor state.

These tests only run when ``PA_TEST_DATABASE_URL`` points at a dedicated
``*_test`` database.  They create and drop their own throwaway databases.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg

from personal_assistant.core.extensions import (
    ExtensionRecord,
    ExtensionRegistry,
    ExtensionState,
    ManifestParser,
)
from personal_assistant.core.extensions.errors import ExtensionError, ExtensionOperationError
from personal_assistant.core.extensions.lifecycle import (
    InstallCoordinator,
    LifecycleManager,
)
from personal_assistant.core.extensions.operations import (
    DIAGNOSTIC_CODES,
    ExtensionOperation,
    OperationState,
)
from personal_assistant.core.extensions.supervision import ExtensionSupervisorService
from personal_assistant.infrastructure.database import (
    PostgresAdapterConfig,
    build_postgres_adapters,
)
from personal_assistant.infrastructure.database.extension_versions import (
    PostgresVersionCatalog,
)
from personal_assistant.infrastructure.database.operations import (
    PostgresExtensionOperationStore,
)
from personal_assistant.infrastructure.extensions.versions import CompatibleVersionOperator

BASE_URL = os.getenv("PA_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres://", "postgresql://"
    )


def _with_db(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, "", ""))


class _Runtime:
    def __init__(self) -> None:
        self.started: list[str] = []

    async def start(self, record: ExtensionRecord) -> None:
        self.started.append(record.manifest.version)

    async def health(self, record: ExtensionRecord) -> bool:
        del record
        return True

    async def drain(self, record: ExtensionRecord, deadline_epoch: float) -> None:
        del record, deadline_epoch

    async def stop(self, record: ExtensionRecord) -> None:
        del record

    async def stop_all(self) -> None:
        return None

    async def invoke_tool(self, *args, **kwargs):  # pragma: no cover - unused
        raise AssertionError("invoke is not part of this test")


class _Installer:
    async def install(self, staged, manifest):  # pragma: no cover - unused
        raise AssertionError("install is not part of this test")

    async def uninstall_code(self, record: ExtensionRecord) -> None:
        del record

    async def clean_failed_install(self, staged) -> None:  # pragma: no cover
        del staged


class _Verifier:
    async def verify(self, installed, manifest) -> None:  # pragma: no cover
        del installed, manifest


class _Stager:
    async def stage(self, source: str):  # pragma: no cover - unused
        raise AssertionError(source)

    async def discard(self, staged) -> None:  # pragma: no cover - unused
        del staged


class _DataStore:
    async def namespaces(self, extension_id: str):
        del extension_id
        return ()

    async def purge(self, extension_id: str) -> None:
        raise ExtensionError("not implemented")


@unittest.skipUnless(BASE_URL, "set PA_TEST_DATABASE_URL to run real PostgreSQL tests")
class PostgresF02Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = _dsn(BASE_URL or "")
        base_name = urlsplit(base).path.lstrip("/")
        if not base_name.endswith("_test"):
            raise RuntimeError(
                "PA_TEST_DATABASE_URL must name a dedicated *_test database"
            )
        self._base = base
        self._admin_dsn = _with_db(base, "postgres")
        self._db_name = f"pa_f02_test_{uuid4().hex}"
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{self._db_name}"')
        finally:
            await admin.close()
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f02_pg_"))
        self._adapters = []

    async def asyncTearDown(self) -> None:
        for adapters in self._adapters:
            with contextlib.suppress(Exception):
                await adapters.close()
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{self._db_name}" WITH (FORCE)')
        finally:
            await admin.close()
        resolved = self.tmp.resolve()
        if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise AssertionError(f"refusing to delete {resolved}")
        shutil.rmtree(resolved, ignore_errors=True)

    async def _new_adapters(self):
        adapters = build_postgres_adapters(
            PostgresAdapterConfig(database_url=_with_db(self._base, self._db_name))
        )
        await adapters.startup()
        self._adapters.append(adapters)
        return adapters

    async def test_operation_store_survives_restart_and_interrupts_running(self) -> None:
        adapters = await self._new_adapters()
        async with adapters.database.connection() as connection:
            rows = await connection.fetch("SELECT version FROM schema_migrations")
        versions = {row["version"] for row in rows}
        self.assertIn("0003_f02_operations", versions)

        operations = PostgresExtensionOperationStore(adapters.database)
        operation = ExtensionOperation(
            id="extop_test_1",
            extension_id="example.echo",
            operation="install",
            status=OperationState.PENDING,
            idempotency_key="restart-key",
            command_fingerprint="a" * 64,
            request_scope="admin:extensions:install",
        )
        await operations.create(operation)
        await operations.update("extop_test_1", status=OperationState.RUNNING)
        with self.assertRaises(ValueError):
            await operations.update(
                "extop_test_1",
                status=OperationState.FAILED,
                diagnostic_code="raw secret text",
            )
        await adapters.close()

        adapters = await self._new_adapters()
        operations = PostgresExtensionOperationStore(adapters.database)
        restored = await operations.get("extop_test_1")
        assert restored is not None
        self.assertEqual(OperationState.RUNNING, restored.status)
        replay = await operations.find_by_request_scope(
            "admin:extensions:install", "restart-key"
        )
        assert replay is not None
        self.assertEqual("a" * 64, replay.command_fingerprint)
        with self.assertRaises(ValueError):
            await operations.interrupt_running(diagnostic_code="raw database text")
        still_running = await operations.get("extop_test_1")
        assert still_running is not None
        self.assertEqual(OperationState.RUNNING, still_running.status)
        interrupted = await operations.interrupt_running(
            diagnostic_code="SUPERVISOR_RESTART"
        )
        self.assertEqual(1, interrupted)
        final = await operations.get("extop_test_1")
        assert final is not None
        self.assertEqual(OperationState.FAILED, final.status)
        self.assertEqual("SUPERVISOR_RESTART", final.diagnostic_code)
        self.assertIn(final.diagnostic_code, DIAGNOSTIC_CODES)
        self.assertIsNone(await operations.get("extop_missing"))

    async def test_request_scope_key_is_unique_across_connections(self) -> None:
        adapters = await self._new_adapters()
        first_store = PostgresExtensionOperationStore(adapters.database)
        second_store = PostgresExtensionOperationStore(adapters.database)
        template = ExtensionOperation(
            id="extop_a",
            extension_id="example.echo",
            operation="install",
            status=OperationState.PENDING,
            idempotency_key="shared-key",
            command_fingerprint="a" * 64,
            request_scope="admin:extensions:install",
        )
        second = ExtensionOperation(
            id="extop_b",
            extension_id="example.echo",
            operation="install",
            status=OperationState.PENDING,
            idempotency_key="shared-key",
            command_fingerprint="a" * 64,
            request_scope="admin:extensions:install",
        )
        results = await asyncio.gather(
            first_store.create(template),
            second_store.create(second),
            return_exceptions=True,
        )
        conflicts = [
            item
            for item in results
            if isinstance(item, ExtensionOperationError) and item.code == "IDEMPOTENCY_CONFLICT"
        ]
        self.assertEqual(1, len(conflicts), results)
        winner = await first_store.find_by_request_scope(
            "admin:extensions:install", "shared-key"
        )
        assert winner is not None
        self.assertIn(winner.id, {"extop_a", "extop_b"})

    async def test_lifecycle_recovery_from_postgres(self) -> None:
        adapters = await self._new_adapters()
        store = adapters.lifecycle_store
        base_manifest = ManifestParser().parse(EXAMPLE)

        def record(name: str, state: ExtensionState, **kwargs) -> ExtensionRecord:
            manifest = replace(base_manifest, id=name, version="0.1.0")
            return ExtensionRecord(
                manifest=manifest,
                artifact_hash="sha256:" + "0" * 64,
                state=state,
                **kwargs,
            )

        await store.save(record("missing.path", ExtensionState.ENABLED))
        await store.save(record("starting.one", ExtensionState.STARTING))
        await store.save(record("draining.one", ExtensionState.DRAINING))
        await store.save(record("upgrading.one", ExtensionState.UPGRADING))
        await store.save(record("uninstalling.one", ExtensionState.UNINSTALLING))
        await adapters.close()

        adapters = await self._new_adapters()
        operations = PostgresExtensionOperationStore(adapters.database)
        registry = ExtensionRegistry()
        runtime = _Runtime()
        service = ExtensionSupervisorService(
            coordinator=InstallCoordinator(
                _Stager(), _Installer(), _Verifier(), adapters.lifecycle_store
            ),
            manager=LifecycleManager(
                adapters.lifecycle_store,
                registry,
                runtime,
                _Installer(),
                _DataStore(),
                CompatibleVersionOperator(
                    PostgresVersionCatalog(adapters.database)
                ),
            ),
            registry=registry,
            store=adapters.lifecycle_store,
            operations=operations,
            runtime=runtime,
        )
        await service.recover()

        expected = {
            "missing.path": ExtensionState.QUARANTINED,
            "starting.one": ExtensionState.QUARANTINED,
            "draining.one": ExtensionState.DISABLED,
            "upgrading.one": ExtensionState.DISABLED,
            "uninstalling.one": ExtensionState.UNINSTALLED,
        }
        for extension_id, state in expected.items():
            record_after = await adapters.lifecycle_store.get(extension_id)
            assert record_after is not None, extension_id
            self.assertEqual(state, record_after.state, extension_id)
        self.assertEqual([], runtime.started)
        self.assertEqual([], list(registry.snapshot.capabilities))
        uninstalled = await adapters.lifecycle_store.get("uninstalling.one")
        assert uninstalled is not None
        self.assertTrue(uninstalled.tombstone)

        await adapters.close()
        adapters = await self._new_adapters()
        again = await adapters.lifecycle_store.get("draining.one")
        assert again is not None
        self.assertEqual(ExtensionState.DISABLED, again.state)
        self.assertEqual(5, len(await adapters.lifecycle_store.all()))

    async def test_version_catalog_retained_versions_and_compatibility(self) -> None:
        adapters = await self._new_adapters()
        store = adapters.lifecycle_store
        base_manifest = ManifestParser().parse(EXAMPLE)

        def versioned(extension_id: str, version: str, schema: int) -> ExtensionRecord:
            path = self.tmp / "installed" / extension_id / version
            path.mkdir(parents=True, exist_ok=True)
            return ExtensionRecord(
                manifest=replace(
                    base_manifest,
                    id=extension_id,
                    version=version,
                    state_schema_version=schema,
                ),
                artifact_hash="sha256:" + "1" * 64,
                state=ExtensionState.DISABLED,
                install_path=str(path),
            )

        await store.save(versioned("example.echo", "0.1.0", 1))
        await store.save(versioned("example.echo", "0.1.5", 2))
        # A version whose install directory is gone is not offered for rollback.
        await store.save(
            ExtensionRecord(
                manifest=replace(
                    base_manifest, id="example.echo", version="0.0.9", state_schema_version=1
                ),
                artifact_hash="sha256:" + "3" * 64,
                state=ExtensionState.DISABLED,
                install_path=str(self.tmp / "installed" / "example.echo" / "0.0.9"),
            )
        )
        await store.save(versioned("example.echo", "0.2.0", 2))

        catalog = PostgresVersionCatalog(adapters.database)
        retained = {item.version for item in await catalog.retained("example.echo")}
        self.assertEqual({"0.1.0", "0.1.5", "0.2.0"}, retained)

        operator = CompatibleVersionOperator(catalog)
        current = await store.get("example.echo")
        assert current is not None
        restored = await operator.rollback(current)
        self.assertEqual("0.1.5", restored.manifest.version)

        await store.save(versioned("solo.one", "0.1.0", 1))
        await store.save(versioned("solo.one", "0.2.0", 2))
        solo = await store.get("solo.one")
        assert solo is not None
        # 0.1.0 is schema 1 while current data is schema 2 -> rollback is unsafe.
        with self.assertRaises(ExtensionError) as captured:
            await operator.rollback(solo)
        self.assertEqual(
            "ROLLBACK_INCOMPATIBLE", getattr(captured.exception, "code", None)
        )


if __name__ == "__main__":
    unittest.main()
