"""F02: real staging + venv + worker subprocess integration for the supervisor.

The tests create their own temporary directories and only clean paths that resolve
inside the system temp root.  A real ``python -m venv`` environment is created for
the example extension; worker crashes and timeouts are injected with small local
worker variants that never touch the network.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from personal_assistant.core.extensions import (
    ExtensionRegistry,
    ExtensionState,
    ManifestParser,
)
from personal_assistant.core.extensions.errors import ExtensionError, RpcCallError
from personal_assistant.core.extensions.lifecycle import (
    InstallationConfirmation,
    InstallCoordinator,
    LifecycleManager,
)
from personal_assistant.core.extensions.operations import OperationState
from personal_assistant.core.extensions.rpc import (
    ExtensionWorker,
    JsonRpcProcessClient,
    WorkerSpec,
)
from personal_assistant.core.extensions.supervision import ExtensionSupervisorService
from personal_assistant.infrastructure.extensions.installer import VenvArtifactInstaller
from personal_assistant.infrastructure.extensions.processes import (
    ProcessContractVerifier,
    ProcessRuntimeSupervisor,
)
from personal_assistant.infrastructure.extensions.staging import LocalArtifactStager
from personal_assistant.infrastructure.extensions.versions import CompatibleVersionOperator
from personal_assistant.infrastructure.memory.extensions import InMemoryLifecycleStore
from personal_assistant.infrastructure.memory.operations import (
    InMemoryExtensionOperationStore,
)
from personal_assistant.infrastructure.memory.versions import InMemoryVersionCatalog

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"

HANGING_HEALTH = '''
    async def health(self) -> HealthReport:
        import asyncio
        await asyncio.sleep(60)
        return HealthReport(healthy=True, status="ready")
'''


def _worker_processes(marker: Path) -> int:
    if not marker.exists():
        return 0
    return len({line for line in marker.read_text("utf-8").splitlines() if line.strip()})


def _safe_rmtree(path: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise AssertionError(f"refusing to delete {resolved}")
    shutil.rmtree(resolved, ignore_errors=True)


def _import_marker(root: Path, name: str) -> Path:
    return root / f"{name}-imports.log"


def _prepared_copy(root: Path, name: str) -> Path:
    """Copy the example extension and make every worker import append to a log.

    The log lives outside the installed payload so it survives installation
    cleanup and proves how many worker processes were actually started.
    """

    destination = root / name
    shutil.copytree(EXAMPLE, destination)
    marker = _import_marker(root, name)
    # The package __init__ imports worker.py, so the same process may run this
    # snippet twice; the process id makes each worker process countable.
    snippet = (
        "\nimport os as _os\n"
        "from pathlib import Path as _Path\n"
        "with _Path(r'" + str(marker) + "').open('a', encoding='utf-8') as _f:\n"
        "    _f.write(str(_os.getpid()) + '\\n')\n"
    )
    worker = destination / "src" / "example_echo" / "worker.py"
    text = worker.read_text("utf-8")
    text = text.replace(
        "from personal_assistant_sdk.worker import run_stdio_worker",
        "from personal_assistant_sdk.worker import run_stdio_worker\n" + snippet,
    )
    worker.write_text(text, encoding="utf-8")
    return destination


class _DataStore:
    async def namespaces(self, extension_id: str):
        del extension_id
        return ()

    async def purge(self, extension_id: str) -> None:
        raise AssertionError("purge is out of scope for F02")


class RealSupervisorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f02_real_"))
        (self.tmp / "staging").mkdir()
        self.source = _prepared_copy(self.tmp, "source")
        self.marker = _import_marker(self.tmp, "source")
        self.registry = ExtensionRegistry()
        self.store = InMemoryLifecycleStore()
        self.operations = InMemoryExtensionOperationStore()
        self.catalog = InMemoryVersionCatalog()
        self.stager = LocalArtifactStager(self.tmp / "staging")
        self.installer = VenvArtifactInstaller(
            install_root=self.tmp / "installed",
            stager=self.stager,
            python_executable=sys.executable,
        )
        self.runtime = ProcessRuntimeSupervisor(
            handshake_timeout_seconds=20.0, health_timeout_seconds=10.0
        )
        coordinator = InstallCoordinator(
            self.stager,
            self.installer,
            ProcessContractVerifier(handshake_timeout_seconds=20.0),
            self.store,
        )
        manager = LifecycleManager(
            self.store,
            self.registry,
            self.runtime,
            self.installer,
            _DataStore(),
            CompatibleVersionOperator(self.catalog),
        )
        self.service = ExtensionSupervisorService(
            coordinator=coordinator,
            manager=manager,
            registry=self.registry,
            store=self.store,
            operations=self.operations,
            runtime=self.runtime,
        )

    async def asyncTearDown(self) -> None:
        await self.runtime.stop_all()
        _safe_rmtree(self.tmp)

    def _confirmation(self, preview) -> InstallationConfirmation:
        return InstallationConfirmation(
            plan_id=preview.plan_id,
            confirmation_nonce=preview.confirmation_nonce,
            preview_hash=preview.preview_hash,
            actor="integration-test",
            confirmed_at=datetime.now(UTC),
            accepted_warning=True,
        )

    async def _run(self, operation):
        final = await self.service.wait_operation(operation.id, timeout_seconds=180.0)
        return final

    async def test_real_venv_worker_install_enable_invoke_disable_uninstall(self) -> None:
        preview = await self.service.inspect(str(self.source))
        self.assertEqual("install", preview.mode)
        # Nothing was executed before the exact confirmation.
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.tmp / "installed").exists())
        self.assertEqual([], list(self.registry.snapshot.capabilities))

        install = await self.service.begin_install(
            preview.plan_id, self._confirmation(preview)
        )
        installed = await self._run(install)
        self.assertEqual(OperationState.SUCCEEDED, installed.status, installed)
        self.assertTrue(self.marker.exists())

        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual(ExtensionState.INSTALLED_DISABLED, record.state)
        install_path = Path(record.install_path or "")
        self.assertTrue((install_path / "payload" / "extension.toml").is_file())
        if sys.platform == "win32":
            venv_python = install_path / "venv" / "Scripts" / "python.exe"
        else:
            venv_python = install_path / "venv" / "bin" / "python"
        self.assertTrue(venv_python.is_file(), venv_python)

        enable = await self.service.begin_enable("example.echo")
        enabled = await self._run(enable)
        self.assertEqual(OperationState.SUCCEEDED, enabled.status, enabled)
        self.assertEqual(
            len(record.manifest.capability_ids), len(self.registry.snapshot.capabilities)
        )

        result = await self.service.invoke_tool(
            "example.echo",
            "example.echo",
            {"text": "real-worker"},
            task_id="task-1",
            run_id="run-1",
            idempotency_key="key-1",
        )
        self.assertEqual("real-worker", result["output"]["echo"])

        disable = await self.service.begin_disable("example.echo", drain_seconds=1.0)
        disabled = await self._run(disable)
        self.assertEqual(OperationState.SUCCEEDED, disabled.status, disabled)
        self.assertEqual([], list(self.registry.snapshot.capabilities))
        with self.assertRaises(ExtensionError):
            await self.service.invoke_tool(
                "example.echo",
                "example.echo",
                {"text": "after-disable"},
                task_id="task-2",
                run_id="run-2",
                idempotency_key="key-2",
            )

        uninstall = await self.service.begin_uninstall("example.echo")
        removed = await self._run(uninstall)
        self.assertEqual(OperationState.SUCCEEDED, removed.status, removed)
        final = await self.service.record("example.echo")
        assert final is not None
        self.assertEqual(ExtensionState.UNINSTALLED, final.state)
        self.assertTrue(final.tombstone)
        self.assertTrue(final.data_retained)
        self.assertFalse(install_path.exists())

    async def test_worker_timeout_stops_the_process_and_is_not_retried(self) -> None:
        source = _prepared_copy(self.tmp, "hanging")
        worker = source / "src" / "example_echo" / "worker.py"
        text = worker.read_text("utf-8")
        start = text.index("    async def health(self) -> HealthReport:")
        end = text.index("    async def drain(self")
        worker.write_text(
            text[:start] + HANGING_HEALTH.strip("\n") + "\n\n" + text[end:],
            encoding="utf-8",
        )
        marker = _import_marker(self.tmp, "hanging")

        preview = await self.service.inspect(str(source))
        self.assertFalse(marker.exists())
        install = await self.service.begin_install(
            preview.plan_id, self._confirmation(preview)
        )
        failed = await self._run(install)
        self.assertEqual(OperationState.FAILED, failed.status)
        self.assertIn(failed.diagnostic_code, {"WORKER_TIMEOUT", "INSTALL_FAILED"})
        attempts = _worker_processes(marker)
        self.assertEqual(1, attempts)
        self.assertEqual(attempts, _worker_processes(marker))
        self.assertFalse((self.tmp / "installed" / "example.echo" / "0.1.0").exists())
        record = await self.service.record("example.echo")
        self.assertIsNotNone(record)
        self.assertNotEqual(ExtensionState.ENABLED, record.state if record else None)

    async def test_concurrent_starts_never_leak_a_second_worker(self) -> None:
        preview = await self.service.inspect(str(self.source))
        install = await self.service.begin_install(
            preview.plan_id, self._confirmation(preview)
        )
        installed = await self._run(install)
        self.assertEqual(OperationState.SUCCEEDED, installed.status, installed)
        record = await self.service.record("example.echo")
        assert record is not None

        await asyncio.gather(
            self.runtime.start(record),
            self.runtime.start(record),
            self.runtime.start(record),
        )
        self.assertTrue(await self.runtime.health(record))
        await self.runtime.stop(record)
        self.assertFalse(await self.runtime.health(record))

    async def test_contract_drift_is_rejected_before_enable(self) -> None:
        cases = (
            (
                "risk-drift",
                "risk=RiskLevel.READ,",
                "risk=RiskLevel.EXTERNAL_WRITE,",
            ),
            (
                "schedule-drift",
                'id="example.echo_hourly",',
                'id="example.wrong_schedule",',
            ),
        )
        for name, old, new in cases:
            with self.subTest(name=name):
                source = _prepared_copy(self.tmp, name)
                worker = source / "src" / "example_echo" / "worker.py"
                text = worker.read_text("utf-8")
                self.assertIn(old, text)
                worker.write_text(text.replace(old, new), encoding="utf-8")
                preview = await self.service.inspect(str(source))
                install = await self.service.begin_install(
                    preview.plan_id, self._confirmation(preview)
                )
                failed = await self._run(install)
                self.assertEqual(OperationState.FAILED, failed.status, failed)
                self.assertEqual("MANIFEST_INVALID", failed.diagnostic_code)
                self.assertEqual([], list(self.registry.snapshot.capabilities))
                version_dir = self.tmp / "installed" / "example.echo" / "0.1.0"
                self.assertFalse(version_dir.exists())

    async def test_handshake_mismatch_never_publishes_capabilities(self) -> None:
        source = _prepared_copy(self.tmp, "mismatch")
        worker = source / "src" / "example_echo" / "worker.py"
        text = worker.read_text("utf-8")
        text = text.replace(
            'slots=("ToolProvider", "ContextProvider", "ScheduleProvider", "FormSchemaProvider"),',
            'slots=("ToolProvider",),',
        )
        worker.write_text(text, encoding="utf-8")
        manifest = ManifestParser().parse(source)
        python_path = str(source / "src")
        if inherited := os.environ.get("PYTHONPATH"):
            python_path = python_path + os.pathsep + inherited
        client = JsonRpcProcessClient(
            WorkerSpec(
                module=manifest.module_name,
                python_executable=sys.executable,
                cwd=str(source),
                environment={"PYTHONPATH": python_path},
            )
        )
        extension = ExtensionWorker(manifest, client)
        try:
            with self.assertRaises(RpcCallError) as captured:
                await extension.start()
            self.assertEqual(-32095, captured.exception.code)
        finally:
            await extension.close()
        self.assertEqual([], list(self.registry.snapshot.capabilities))


if __name__ == "__main__":
    unittest.main()
