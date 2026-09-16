"""F02: cancellation must clean up code, staging and installer subprocesses."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path

from personal_assistant.core.extensions import (
    ConfirmationRequiredError,
    ExtensionRecord,
    ExtensionState,
)
from personal_assistant.core.extensions.async_utils import run_blocking
from personal_assistant.core.extensions.lifecycle import (
    InstallationConfirmation,
    InstallCoordinator,
    InstalledArtifact,
    StagedArtifact,
)
from personal_assistant.infrastructure.extensions.installer import VenvArtifactInstaller
from personal_assistant.infrastructure.extensions.staging import LocalArtifactStager

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"


def _safe_rmtree(path: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise AssertionError(f"refusing to delete {resolved}")
    shutil.rmtree(resolved, ignore_errors=True)


def _process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return f"{pid}" in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class CancellingInstaller:
    def __init__(self, root: Path, *, cancel_at: str | None = None) -> None:
        self._root = root
        self.cancel_at = cancel_at
        self.removed: list[str] = []

    async def install(self, staged: StagedArtifact, manifest) -> InstalledArtifact:
        target = self._root / "installed" / manifest.version
        (target / "payload").mkdir(parents=True, exist_ok=True)
        (target / "venv").mkdir(parents=True, exist_ok=True)
        if self.cancel_at == "install":
            # The real installer rolls back its own destination before raising.
            shutil.rmtree(target, ignore_errors=True)
            raise asyncio.CancelledError
        return InstalledArtifact(target, "runtime-test")

    async def uninstall_code(self, record: ExtensionRecord) -> None:
        del record

    async def remove_version(self, record: ExtensionRecord) -> None:
        if record.install_path:
            self.removed.append(record.install_path)
            shutil.rmtree(Path(record.install_path), ignore_errors=True)

    async def clean_failed_install(self, staged: StagedArtifact) -> None:
        del staged


class CancellingVerifier:
    def __init__(self, *, cancel: bool = False) -> None:
        self.cancel = cancel
        self.verified = 0

    async def verify(self, installed, manifest) -> None:
        del installed, manifest
        if self.cancel:
            raise asyncio.CancelledError
        self.verified += 1


class CancellingDiscardStager:
    """Delegates to a real stager but cancels the first discard call."""

    def __init__(self, inner: LocalArtifactStager) -> None:
        self._inner = inner
        self.cancel_next_discard = False

    async def stage(self, source: str) -> StagedArtifact:
        return await self._inner.stage(source)

    async def discard(self, staged: StagedArtifact) -> None:
        if self.cancel_next_discard:
            self.cancel_next_discard = False
            raise asyncio.CancelledError
        await self._inner.discard(staged)


class GatedInstaller(CancellingInstaller):
    """Creates the version directory, then blocks until cancelled."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.entered = asyncio.Event()

    async def install(self, staged: StagedArtifact, manifest) -> InstalledArtifact:
        target = self._root / "installed" / manifest.version
        (target / "payload").mkdir(parents=True, exist_ok=True)
        (target / "venv").mkdir(parents=True, exist_ok=True)
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        raise AssertionError("unreachable")


class Store:
    def __init__(self) -> None:
        self.records: dict[str, ExtensionRecord] = {}
        self.cancel_final_save = False

    async def get(self, extension_id: str):
        return self.records.get(extension_id)

    async def all(self):
        return tuple(self.records.values())

    async def save(self, record: ExtensionRecord) -> None:
        if record.state is ExtensionState.INSTALLED_DISABLED and self.cancel_final_save:
            self.cancel_final_save = False
            raise asyncio.CancelledError
        self.records[record.manifest.id] = record


class InstallCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f02_cancel_"))
        self.stager = LocalArtifactStager(self.tmp / "staging")
        self.installer = CancellingInstaller(self.tmp)
        self.verifier = CancellingVerifier()
        self.store = Store()
        self.now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        self.coordinator = InstallCoordinator(
            self.stager, self.installer, self.verifier, self.store, clock=lambda: self.now
        )

    def tearDown(self) -> None:
        _safe_rmtree(self.tmp)

    def _confirmation(self, preview) -> InstallationConfirmation:
        return InstallationConfirmation(
            plan_id=preview.plan_id,
            confirmation_nonce=preview.confirmation_nonce,
            preview_hash=preview.preview_hash,
            actor="local-owner",
            confirmed_at=self.now,
            accepted_warning=True,
        )

    def _staged_roots(self) -> list[Path]:
        root = self.tmp / "staging"
        return list(root.iterdir()) if root.exists() else []

    async def test_cancel_during_install_removes_the_version_and_staging(self) -> None:
        self.installer.cancel_at = "install"
        preview = await self.coordinator.prepare(str(EXAMPLE))
        with self.assertRaises(asyncio.CancelledError):
            await self.coordinator.install(preview.plan_id, self._confirmation(preview))
        self.assertFalse((self.tmp / "installed" / "0.1.0").exists())
        self.assertEqual([], self._staged_roots())
        self.assertEqual(ExtensionState.REJECTED, self.store.records["example.echo"].state)
        # The consumed plan cannot be installed twice.
        with self.assertRaises(ConfirmationRequiredError):
            await self.coordinator.install(preview.plan_id, self._confirmation(preview))

    async def test_cancel_during_contract_verification_cleans_everything(self) -> None:
        self.verifier.cancel = True
        preview = await self.coordinator.prepare(str(EXAMPLE))
        with self.assertRaises(asyncio.CancelledError):
            await self.coordinator.install(preview.plan_id, self._confirmation(preview))
        self.assertFalse((self.tmp / "installed" / "0.1.0").exists())
        self.assertEqual([], self._staged_roots())
        self.assertEqual(ExtensionState.REJECTED, self.store.records["example.echo"].state)

    async def test_cancel_during_staging_discard_still_cleans_the_version(self) -> None:
        stager = CancellingDiscardStager(LocalArtifactStager(self.tmp / "staging2"))
        coordinator = InstallCoordinator(
            stager, self.installer, self.verifier, self.store, clock=lambda: self.now
        )
        preview = await coordinator.prepare(str(EXAMPLE))
        stager.cancel_next_discard = True
        with self.assertRaises(asyncio.CancelledError):
            await coordinator.install(preview.plan_id, self._confirmation(preview))
        self.assertFalse((self.tmp / "installed" / "0.1.0").exists())
        self.assertEqual([], list((self.tmp / "staging2").iterdir()))
        self.assertEqual(ExtensionState.REJECTED, self.store.records["example.echo"].state)

    async def test_cancel_during_final_save_removes_the_unregistered_version(self) -> None:
        self.store.cancel_final_save = True
        preview = await self.coordinator.prepare(str(EXAMPLE))
        with self.assertRaises(asyncio.CancelledError):
            await self.coordinator.install(preview.plan_id, self._confirmation(preview))
        self.assertFalse((self.tmp / "installed" / "0.1.0").exists())
        self.assertEqual([], self._staged_roots())
        self.assertEqual(ExtensionState.REJECTED, self.store.records["example.echo"].state)

    async def test_cancelling_real_install_removes_the_partial_version(self) -> None:
        class SlowVenvInstaller(VenvArtifactInstaller):
            async def _create_venv(self, venv: Path, *, with_pip: bool) -> None:
                await asyncio.sleep(0.5)
                await super()._create_venv(venv, with_pip=with_pip)

        installer = SlowVenvInstaller(
            install_root=self.tmp / "installed", stager=self.stager
        )
        manifest = self._manifest()
        staged = await self.stager.stage(str(EXAMPLE))
        task = asyncio.create_task(installer.install(staged, manifest))
        await asyncio.sleep(0.1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse((self.tmp / "installed" / "example.echo" / "0.1.0").exists())

    def _manifest(self):
        from personal_assistant.core.extensions import ManifestParser

        return ManifestParser().parse(EXAMPLE)

    async def test_cancel_during_payload_copy_waits_then_removes_destination(self) -> None:
        class SlowCopyInstaller(VenvArtifactInstaller):
            @staticmethod
            def copy_payload(source: Path, payload: Path) -> None:
                VenvArtifactInstaller.copy_payload(source, payload)
                time.sleep(0.4)
                (payload / "late.txt").write_text("late", encoding="utf-8")

        installer = SlowCopyInstaller(
            install_root=self.tmp / "installed", stager=self.stager
        )
        manifest = self._manifest()
        staged = await self.stager.stage(str(EXAMPLE))
        task = asyncio.create_task(installer.install(staged, manifest))
        await asyncio.sleep(0.1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        destination = self.tmp / "installed" / "example.echo" / "0.1.0"
        # The copy thread had time to recreate files before the rmtree; it must
        # have been awaited first.
        await asyncio.sleep(0.6)
        self.assertFalse(destination.exists())
        self.assertEqual([], list((self.tmp / "installed").rglob("late.txt")))

    async def test_run_blocking_cancellation_survives_a_late_thread_failure(self) -> None:
        release = threading.Event()

        def failing() -> None:
            release.wait(5)
            raise RuntimeError("late blocking failure")

        task = asyncio.create_task(run_blocking(failing))
        await asyncio.sleep(0.1)
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_run_blocking_survives_repeated_cancellation(self) -> None:
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow() -> None:
            started.set()
            release.wait(5)
            finished.set()

        task = asyncio.create_task(run_blocking(slow))
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.05)
        # The second cancellation must not stop the wait for the thread.
        self.assertFalse(finished.is_set())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())

    async def test_cancelling_contract_verification_closes_the_worker(self) -> None:
        from unittest.mock import patch

        from personal_assistant.core.extensions import ManifestParser
        from personal_assistant.infrastructure.extensions import processes

        class FakeWorker:
            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.closed = 0

            async def start(self, *, timeout_seconds: float) -> None:
                del timeout_seconds
                self.started.set()
                await asyncio.sleep(30)

            async def close(self) -> None:
                self.closed += 1

        worker = FakeWorker()
        installed = InstalledArtifact(self.tmp / "installed" / "0.1.0", "rt")
        manifest = ManifestParser().parse(EXAMPLE)
        verifier = processes.ProcessContractVerifier()
        with patch.object(processes, "_build_worker", return_value=worker):
            task = asyncio.create_task(verifier.verify(installed, manifest))
            await asyncio.wait_for(worker.started.wait(), timeout=5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(1, worker.closed)

    async def test_cancelling_a_real_installer_command_reaps_the_child(self) -> None:
        installer = VenvArtifactInstaller(
            install_root=self.tmp / "installed", stager=self.stager
        )
        pid_file = self.tmp / "child.pid"
        script = (
            "import os,time;"
            f"open(r'{pid_file}','w').write(str(os.getpid()));"
            "time.sleep(30)"
        )
        task = asyncio.create_task(
            installer._run_capture(  # noqa: SLF001 - the cancellation path under test
                [sys.executable, "-c", script], timeout=60
            )
        )
        deadline = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < deadline:
            content = pid_file.read_text("utf-8").strip() if pid_file.exists() else ""
            if content.isdigit():
                break
            await asyncio.sleep(0.05)
        self.assertTrue(pid_file.exists(), "installer child never started")
        pid = int(pid_file.read_text("utf-8").strip())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        deadline = asyncio.get_event_loop().time() + 5
        while _process_alive(pid) and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.1)
        self.assertFalse(_process_alive(pid), f"installer child {pid} is still alive")

class _Runtime:
    async def start(self, record) -> None:
        del record

    async def health(self, record) -> bool:
        del record
        return True

    async def drain(self, record, deadline_epoch):
        del record, deadline_epoch
        return {"drained": True, "active_calls": 0}

    async def stop(self, record) -> None:
        del record

    async def stop_all(self) -> None:
        return None

    async def invoke_tool(self, *args, **kwargs):
        raise AssertionError("invoke is not part of this test")


class _DataStore:
    async def namespaces(self, extension_id):
        del extension_id
        return ()

    async def purge(self, extension_id):
        raise AssertionError(f"purge is not implemented for {extension_id}")


class ServiceCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from personal_assistant.core.extensions import ExtensionRegistry
        from personal_assistant.core.extensions.lifecycle import LifecycleManager
        from personal_assistant.core.extensions.supervision import (
            ExtensionSupervisorService,
        )
        from personal_assistant.infrastructure.extensions.versions import (
            CompatibleVersionOperator,
        )
        from personal_assistant.infrastructure.memory.extensions import (
            InMemoryLifecycleStore,
        )
        from personal_assistant.infrastructure.memory.operations import (
            InMemoryExtensionOperationStore,
        )
        from personal_assistant.infrastructure.memory.versions import (
            InMemoryVersionCatalog,
        )

        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f02_svc_cancel_"))
        self.stager = LocalArtifactStager(self.tmp / "staging")
        self.installer = GatedInstaller(self.tmp)
        self.store = InMemoryLifecycleStore()
        self.operations = InMemoryExtensionOperationStore()
        self.registry = ExtensionRegistry()
        self.runtime = _Runtime()
        self.coordinator = InstallCoordinator(
            self.stager, self.installer, CancellingVerifier(), self.store
        )
        self.manager = LifecycleManager(
            self.store,
            self.registry,
            self.runtime,
            self.installer,
            _DataStore(),
            CompatibleVersionOperator(InMemoryVersionCatalog()),
        )
        self.service = ExtensionSupervisorService(
            coordinator=self.coordinator,
            manager=self.manager,
            registry=self.registry,
            store=self.store,
            operations=self.operations,
            runtime=self.runtime,
        )

    async def asyncTearDown(self) -> None:
        await self.service.stop_all()
        _safe_rmtree(self.tmp)

    async def test_stop_all_mid_install_cancels_cleans_and_records_failure(self) -> None:
        from personal_assistant.core.extensions import OperationState

        preview = await self.service.inspect(str(EXAMPLE))
        confirmation = InstallationConfirmation(
            plan_id=preview.plan_id,
            confirmation_nonce=preview.confirmation_nonce,
            preview_hash=preview.preview_hash,
            actor="local-owner",
            confirmed_at=datetime.now(UTC),
            accepted_warning=True,
        )
        operation = await self.service.begin_install(preview.plan_id, confirmation)
        await asyncio.wait_for(self.installer.entered.wait(), timeout=10)
        await self.service.stop_all()
        final = await self.service.operation(operation.id)
        assert final is not None
        self.assertEqual(OperationState.FAILED, final.status)
        self.assertEqual("OPERATION_CANCELLED", final.diagnostic_code)
        self.assertFalse((self.tmp / "installed" / "0.1.0").exists())
        self.assertEqual([], list((self.tmp / "staging").iterdir()))
        record = await self.store.get("example.echo")
        assert record is not None
        self.assertEqual(ExtensionState.REJECTED, record.state)


if __name__ == "__main__":
    unittest.main()
