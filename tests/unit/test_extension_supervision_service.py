"""F02: supervisor service state machine (install/enable/disable/upgrade/rollback)."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_assistant.core.extensions import (
    ExtensionRecord,
    ExtensionRegistry,
    ExtensionState,
    ManifestParser,
    compute_artifact_hash,
)
from personal_assistant.core.extensions.errors import (
    ExtensionError,
    ExtensionOperationError,
    RpcCallError,
    RpcTimeoutError,
)
from personal_assistant.core.extensions.lifecycle import (
    InstallationConfirmation,
    InstallCoordinator,
    LifecycleManager,
    StagedArtifact,
)
from personal_assistant.core.extensions.operations import DIAGNOSTIC_CODES, OperationState
from personal_assistant.core.extensions.supervision import ExtensionSupervisorService
from personal_assistant.infrastructure.extensions.versions import (
    CompatibleVersionOperator,
)
from personal_assistant.infrastructure.memory.extensions import InMemoryLifecycleStore
from personal_assistant.infrastructure.memory.operations import (
    InMemoryExtensionOperationStore,
)
from personal_assistant.infrastructure.memory.versions import InMemoryVersionCatalog

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"


def _safe_rmtree(path: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise AssertionError(f"refusing to delete {resolved}")
    shutil.rmtree(resolved, ignore_errors=True)


def _stage_copy(root: Path, *, version: str, schema: int = 1) -> Path:
    destination = root / f"artifact-{version}-{schema}"
    shutil.copytree(EXAMPLE, destination)
    manifest_path = destination / "extension.toml"
    text = manifest_path.read_text("utf-8")
    text = text.replace('version = "0.1.0"', f'version = "{version}"')
    text = text.replace("state_schema_version = 1", f"state_schema_version = {schema}")
    manifest_path.write_text(text, encoding="utf-8")
    return destination


class CopyingStager:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def stage(self, source: str) -> StagedArtifact:
        target = self._root / f"staged-{len(list(self._root.iterdir()))}"
        shutil.copytree(Path(source), target)
        return StagedArtifact(str(source), target, compute_artifact_hash(target))

    async def discard(self, staged: StagedArtifact) -> None:
        _safe_rmtree(staged.root)


class FakeInstaller:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.installed: list[str] = []
        self.uninstalled: list[str] = []
        self.cleaned = 0

    async def install(self, staged: StagedArtifact, manifest) -> Any:
        del staged
        target = self.root / manifest.id / manifest.version
        (target / "payload").mkdir(parents=True, exist_ok=True)
        (target / "venv").mkdir(parents=True, exist_ok=True)
        self.installed.append(manifest.version)
        from personal_assistant.core.extensions.lifecycle import InstalledArtifact

        return InstalledArtifact(target, f"runtime-{manifest.version}")

    async def uninstall_code(self, record: ExtensionRecord) -> None:
        if record.install_path:
            self.uninstalled.append(record.install_path)
            path = Path(record.install_path)
            if path.exists():
                shutil.rmtree(path)

    async def remove_version(self, record: ExtensionRecord) -> None:
        await self.uninstall_code(record)

    async def clean_failed_install(self, staged: StagedArtifact) -> None:
        del staged
        self.cleaned += 1


class FakeVerifier:
    def __init__(self) -> None:
        self.verified: list[tuple[str, str]] = []
        self.fail_next = False

    async def verify(self, installed, manifest) -> None:
        if self.fail_next:
            self.fail_next = False
            raise ExtensionError("contract verification failed")
        self.verified.append((manifest.id, manifest.version))


class FakeRuntime:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.drained: list[tuple[str, float]] = []
        self.invocations: list[tuple[str, str, dict[str, Any]]] = []
        # Extension id -> running version.  stop/stop_all actually clear the
        # running worker so invoke_tool fails when nothing serves the extension.
        self.active: dict[str, str] = {}
        self.healthy = True
        self.fail_start_versions: set[str] = set()
        self.unhealthy_versions: set[str] = set()
        self.blocked_start_versions: set[str] = set()
        self.start_blockers: dict[str, asyncio.Event] = {}
        self.fail_next_invoke = False
        self.stop_all_called = 0
        self.drain_report: dict[str, Any] | None = None
        self.fail_drain = False

    async def start(self, record: ExtensionRecord) -> None:
        version = record.manifest.version
        if version in self.fail_start_versions:
            raise ExtensionError("worker start failed")
        if version in self.blocked_start_versions:
            await self.start_blockers.setdefault(version, asyncio.Event()).wait()
        self.started.append(version)
        self.active[record.manifest.id] = version

    async def health(self, record: ExtensionRecord) -> bool:
        if record.manifest.version in self.unhealthy_versions:
            return False
        return self.healthy

    async def drain(self, record: ExtensionRecord, deadline_epoch: float):
        self.drained.append((record.manifest.id, deadline_epoch))
        if self.fail_drain:
            raise RpcTimeoutError("injected drain timeout")
        return self.drain_report

    async def stop(self, record: ExtensionRecord) -> None:
        self.stopped.append(record.manifest.version)
        if self.active.get(record.manifest.id) == record.manifest.version:
            del self.active[record.manifest.id]

    async def stop_all(self) -> None:
        self.stop_all_called += 1
        self.active.clear()

    async def invoke_tool(
        self,
        extension_id: str,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        task_id: str,
        run_id: str,
        idempotency_key: str,
        deadline_seconds: float,
    ) -> dict[str, Any]:
        del task_id, run_id, idempotency_key, deadline_seconds
        if extension_id not in self.active:
            raise RpcCallError(-32090, "worker is not running")
        if self.fail_next_invoke:
            self.fail_next_invoke = False
            raise RpcCallError(-32091, "worker exited unexpectedly")
        self.invocations.append((extension_id, tool_id, arguments))
        return {"outcome": "SUCCEEDED", "output": {"echo": arguments.get("text", "")}}


class FlakyVersionOperator:
    def __init__(self, inner: CompatibleVersionOperator) -> None:
        self._inner = inner
        self.fail_next_activate = False

    async def activate(self, old, candidate) -> None:
        if self.fail_next_activate:
            self.fail_next_activate = False
            raise ExtensionError("activation failed")
        await self._inner.activate(old, candidate)

    async def rollback(self, current) -> ExtensionRecord:
        return await self._inner.rollback(current)


class FailingLifecycleStore(InMemoryLifecycleStore):
    """In-memory store that can fail the next ENABLED save once."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_next_enabled_save = False

    async def save(self, record: ExtensionRecord) -> None:
        if self.fail_next_enabled_save and record.state is ExtensionState.ENABLED:
            self.fail_next_enabled_save = False
            raise ExtensionError("lifecycle store write failed")
        await super().save(record)


class RecordingRegistry(ExtensionRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.history: list[dict[str, str]] = []

    def enable(self, record: ExtensionRecord):
        snapshot = super().enable(record)
        self._record(snapshot)
        return snapshot

    def disable(self, extension_id: str):
        snapshot = super().disable(extension_id)
        self._record(snapshot)
        return snapshot

    def replace_all(self, records):
        snapshot = super().replace_all(records)
        self._record(snapshot)
        return snapshot

    def _record(self, snapshot) -> None:
        self.history.append(
            {
                extension_id: record.manifest.version
                for extension_id, record in snapshot.extensions.items()
            }
        )


class SupervisorServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f02_service_"))
        (self.tmp / "staging").mkdir()
        self.registry = RecordingRegistry()
        self.store = InMemoryLifecycleStore()
        self.operations = InMemoryExtensionOperationStore()
        self.runtime = FakeRuntime()
        self.installer = FakeInstaller(self.tmp / "installed")
        self.verifier = FakeVerifier()
        self.catalog = InMemoryVersionCatalog()
        self.operator = FlakyVersionOperator(CompatibleVersionOperator(self.catalog))
        self.now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        self._rebuild_service()

    def _rebuild_service(self) -> None:
        coordinator = InstallCoordinator(
            CopyingStager(self.tmp / "staging"),
            self.installer,
            self.verifier,
            self.store,
            clock=lambda: self.now,
        )
        manager = LifecycleManager(
            self.store,
            self.registry,
            self.runtime,
            self.installer,
            InMemoryExtensionDataStoreForTests(),
            self.operator,
            clock=lambda: self.now,
        )
        self.manager = manager
        self.service = ExtensionSupervisorService(
            coordinator=coordinator,
            manager=manager,
            registry=self.registry,
            store=self.store,
            operations=self.operations,
            runtime=self.runtime,
            clock=lambda: self.now,
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

    async def _install_and_enable(self, source: Path) -> None:
        preview = await self.service.inspect(str(source))
        operation = await self.service.begin_install(preview.plan_id, self._confirmation(preview))
        final = await self.service.wait_operation(operation.id)
        self.assertEqual(OperationState.SUCCEEDED, final.status, final)
        enable = await self.service.begin_enable("example.echo")
        enabled = await self.service.wait_operation(enable.id)
        self.assertEqual(OperationState.SUCCEEDED, enabled.status, enabled)

    async def test_inspect_stages_without_execution(self) -> None:
        preview = await self.service.inspect(str(EXAMPLE))
        self.assertEqual("install", preview.mode)
        self.assertEqual([], self.installer.installed)
        self.assertEqual([], self.verifier.verified)
        self.assertEqual(0, len(self.registry.snapshot.capabilities))

    async def test_recover_rejects_leftover_staging_and_quarantines_missing_paths(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        leftover = self.tmp / "staging" / "leftover"
        shutil.copytree(EXAMPLE, leftover)
        await self.store.save(
            ExtensionRecord(
                manifest=replace(manifest, root=leftover),
                artifact_hash=compute_artifact_hash(leftover),
                state=ExtensionState.STAGED,
            )
        )
        await self.store.save(
            ExtensionRecord(
                manifest=replace(manifest, id="broken.path", version="0.1.0"),
                artifact_hash="sha256:" + "0" * 64,
                state=ExtensionState.ENABLED,
                install_path=None,
            )
        )
        await self.service.recover()
        staged = await self.service.record("example.echo")
        assert staged is not None
        self.assertEqual(ExtensionState.REJECTED, staged.state)
        self.assertFalse(leftover.exists())
        broken = await self.service.record("broken.path")
        assert broken is not None
        self.assertEqual(ExtensionState.QUARANTINED, broken.state)
        self.assertEqual([], list(self.registry.snapshot.capabilities))

    async def test_install_enable_invoke_disable_uninstall_flow(self) -> None:
        await self._install_and_enable(EXAMPLE)
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual(ExtensionState.ENABLED, record.state)
        self.assertEqual(
            len(record.manifest.capability_ids), len(self.registry.snapshot.capabilities)
        )
        owner = self.registry.snapshot.owner_of("example.echo")
        self.assertIsNotNone(owner)
        self.assertEqual("0.1.0", owner.extension_version if owner else None)

        result = await self.service.invoke_tool(
            "example.echo",
            "example.echo",
            {"text": "hello"},
            task_id="task-1",
            run_id="run-1",
            idempotency_key="key-1",
        )
        self.assertEqual("hello", result["output"]["echo"])
        self.assertEqual(1, len(self.runtime.invocations))

        disable = await self.service.begin_disable("example.echo", drain_seconds=0.5)
        disabled = await self.service.wait_operation(disable.id)
        self.assertEqual(OperationState.SUCCEEDED, disabled.status)
        self.assertEqual([], list(self.registry.snapshot.capabilities))
        self.assertEqual(1, len(self.runtime.drained))
        self.assertTrue(self.runtime.stopped)
        with self.assertRaises(ExtensionError):
            await self.service.invoke_tool(
                "example.echo",
                "example.echo",
                {"text": "blocked"},
                task_id="task-2",
                run_id="run-2",
                idempotency_key="key-2",
            )
        self.assertEqual(1, len(self.runtime.invocations))

        uninstall = await self.service.begin_uninstall("example.echo")
        removed = await self.service.wait_operation(uninstall.id)
        self.assertEqual(OperationState.SUCCEEDED, removed.status)
        final = await self.service.record("example.echo")
        assert final is not None
        self.assertEqual(ExtensionState.UNINSTALLED, final.state)
        self.assertTrue(final.tombstone)
        self.assertTrue(final.data_retained)
        self.assertEqual(1, len(self.installer.uninstalled))

    async def test_disabled_extension_never_accepts_new_calls(self) -> None:
        await self._install_and_enable(EXAMPLE)
        disable = await self.service.begin_disable("example.echo")
        final = await self.service.wait_operation(disable.id)
        self.assertEqual(OperationState.SUCCEEDED, final.status, final)
        with self.assertRaises(ExtensionError):
            await self.service.invoke_tool(
                "example.echo",
                "example.echo",
                {"text": "x"},
                task_id="t",
                run_id="r",
                idempotency_key="k",
            )

    async def test_unhealthy_enable_quarantines_and_can_be_repaired(self) -> None:
        preview = await self.service.inspect(str(EXAMPLE))
        install = await self.service.begin_install(preview.plan_id, self._confirmation(preview))
        await self.service.wait_operation(install.id)

        self.runtime.healthy = False
        enable = await self.service.begin_enable("example.echo")
        failed = await self.service.wait_operation(enable.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        self.assertEqual("HEALTHCHECK_FAILED", failed.diagnostic_code)
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual(ExtensionState.QUARANTINED, record.state)
        self.assertEqual([], list(self.registry.snapshot.capabilities))
        self.assertIn("0.1.0", self.runtime.stopped)

        self.runtime.healthy = True
        repair = await self.service.begin_enable("example.echo")
        repaired = await self.service.wait_operation(repair.id)
        self.assertEqual(OperationState.SUCCEEDED, repaired.status, repaired)
        self.assertEqual(ExtensionState.ENABLED, (await self.service.record("example.echo")).state)

    async def test_worker_crash_quarantines_without_host_failure(self) -> None:
        await self._install_and_enable(EXAMPLE)
        self.runtime.fail_next_invoke = True
        with self.assertRaises(ExtensionError):
            await self.service.invoke_tool(
                "example.echo",
                "example.echo",
                {"text": "boom"},
                task_id="t",
                run_id="r",
                idempotency_key="k",
            )
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual(ExtensionState.QUARANTINED, record.state)
        self.assertEqual([], list(self.registry.snapshot.capabilities))
        # Other supervisor operations still work after a worker crash.
        self.assertIsNotNone(await self.service.records())

    async def test_enable_publishes_one_complete_snapshot(self) -> None:
        await self._install_and_enable(EXAMPLE)
        for snapshot in self.registry.history:
            versions = list(snapshot.values())
            self.assertIn(len(versions), (0, 1))
        self.assertEqual({"example.echo": "0.1.0"}, self.registry.history[-1])

    async def test_duplicate_capability_quarantines_without_partial_registry(self) -> None:
        from personal_assistant.core.extensions.manifest import ManifestTool

        base = ManifestParser().parse(EXAMPLE)
        other_manifest = replace(
            base,
            id="other.echo",
            tools=(
                ManifestTool(
                    id="example.echo",
                    risk=base.tools[0].risk,
                    input_schema=base.tools[0].input_schema,
                    output_schema=base.tools[0].output_schema,
                ),
            ),
        )
        self.registry.enable(
            ExtensionRecord(
                manifest=other_manifest,
                artifact_hash="sha256:" + "0" * 64,
                state=ExtensionState.ENABLED,
            )
        )
        preview = await self.service.inspect(str(EXAMPLE))
        install = await self.service.begin_install(preview.plan_id, self._confirmation(preview))
        await self.service.wait_operation(install.id)
        enable = await self.service.begin_enable("example.echo")
        failed = await self.service.wait_operation(enable.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual(ExtensionState.QUARANTINED, record.state)
        self.assertEqual({"other.echo"}, set(self.registry.snapshot.extensions))

    async def test_failed_verification_rejects_candidate_without_touching_old(self) -> None:
        await self._install_and_enable(EXAMPLE)
        self.verifier.fail_next = True
        preview = await self.service.inspect(str(_stage_copy(self.tmp, version="0.2.0")))
        upgrade = await self.service.begin_upgrade(
            "example.echo", preview.plan_id, self._confirmation(preview)
        )
        failed = await self.service.wait_operation(upgrade.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual(ExtensionState.ENABLED, record.state)
        self.assertEqual("0.1.0", record.manifest.version)
        owner = self.registry.snapshot.owner_of("example.echo")
        self.assertEqual("0.1.0", owner.extension_version if owner else None)

    async def test_successful_upgrade_switches_atomically_and_rollback_restores(self) -> None:
        await self._install_and_enable(EXAMPLE)
        preview = await self.service.inspect(str(_stage_copy(self.tmp, version="0.2.0")))
        self.assertEqual("upgrade", preview.mode)
        upgrade = await self.service.begin_upgrade(
            "example.echo", preview.plan_id, self._confirmation(preview)
        )
        final = await self.service.wait_operation(upgrade.id)
        self.assertEqual(OperationState.SUCCEEDED, final.status, final)
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual("0.2.0", record.manifest.version)
        self.assertEqual(ExtensionState.ENABLED, record.state)
        owner = self.registry.snapshot.owner_of("example.echo")
        self.assertEqual("0.2.0", owner.extension_version if owner else None)
        for snapshot in self.registry.history:
            self.assertLessEqual(len(snapshot), 1)
        await self.service.invoke_tool(
            "example.echo",
            "example.echo",
            {"text": "v2"},
            task_id="t",
            run_id="r",
            idempotency_key="k",
        )
        self.assertEqual("v2", self.runtime.invocations[-1][2]["text"])

        rollback = await self.service.begin_rollback("example.echo")
        rolled = await self.service.wait_operation(rollback.id)
        self.assertEqual(OperationState.SUCCEEDED, rolled.status, rolled)
        restored = await self.service.record("example.echo")
        assert restored is not None
        self.assertEqual("0.1.0", restored.manifest.version)
        self.assertEqual(ExtensionState.ENABLED, restored.state)
        owner = self.registry.snapshot.owner_of("example.echo")
        self.assertEqual("0.1.0", owner.extension_version if owner else None)

    async def test_incompatible_rollback_keeps_current_version_enabled(self) -> None:
        await self._install_and_enable(EXAMPLE)
        preview = await self.service.inspect(
            str(_stage_copy(self.tmp, version="0.2.0", schema=2))
        )
        upgrade = await self.service.begin_upgrade(
            "example.echo", preview.plan_id, self._confirmation(preview)
        )
        self.assertEqual(
            OperationState.SUCCEEDED, (await self.service.wait_operation(upgrade.id)).status
        )
        rollback = await self.service.begin_rollback("example.echo")
        failed = await self.service.wait_operation(rollback.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        self.assertEqual("ROLLBACK_INCOMPATIBLE", failed.diagnostic_code)
        # The failed rollback must not take the current version down.
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual("0.2.0", record.manifest.version)
        self.assertEqual(ExtensionState.ENABLED, record.state)
        owner = self.registry.snapshot.owner_of("example.echo")
        self.assertEqual("0.2.0", owner.extension_version if owner else None)
        result = await self.service.invoke_tool(
            "example.echo",
            "example.echo",
            {"text": "still-v2"},
            task_id="t",
            run_id="r",
            idempotency_key="k",
        )
        self.assertEqual("still-v2", result["output"]["echo"])

    async def test_missing_rollback_candidate_keeps_current_version_enabled(self) -> None:
        await self._install_and_enable(EXAMPLE)
        # No upgrade ever ran, so the retained-version catalog is empty.
        rollback = await self.service.begin_rollback("example.echo")
        failed = await self.service.wait_operation(rollback.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        self.assertEqual("ROLLBACK_NO_CANDIDATE", failed.diagnostic_code)
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual("0.1.0", record.manifest.version)
        self.assertEqual(ExtensionState.ENABLED, record.state)
        owner = self.registry.snapshot.owner_of("example.echo")
        self.assertEqual("0.1.0", owner.extension_version if owner else None)
        result = await self.service.invoke_tool(
            "example.echo",
            "example.echo",
            {"text": "still-here"},
            task_id="t",
            run_id="r",
            idempotency_key="k",
        )
        self.assertEqual("still-here", result["output"]["echo"])

    async def _install_enable_and_upgrade(self) -> None:
        await self._install_and_enable(EXAMPLE)
        preview = await self.service.inspect(str(_stage_copy(self.tmp, version="0.2.0")))
        upgrade = await self.service.begin_upgrade(
            "example.echo", preview.plan_id, self._confirmation(preview)
        )
        final = await self.service.wait_operation(upgrade.id)
        self.assertEqual(OperationState.SUCCEEDED, final.status, final)

    async def _assert_version_serving(self, version: str, text: str) -> None:
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual(version, record.manifest.version)
        self.assertEqual(ExtensionState.ENABLED, record.state)
        owner = self.registry.snapshot.owner_of("example.echo")
        self.assertEqual(version, owner.extension_version if owner else None)
        result = await self.service.invoke_tool(
            "example.echo",
            "example.echo",
            {"text": text},
            task_id="t",
            run_id="r",
            idempotency_key="k",
        )
        self.assertEqual(text, result["output"]["echo"])

    async def test_rollback_candidate_start_failure_keeps_current_version_enabled(
        self,
    ) -> None:
        await self._install_enable_and_upgrade()
        self.runtime.fail_start_versions.add("0.1.0")
        rollback = await self.service.begin_rollback("example.echo")
        failed = await self.service.wait_operation(rollback.id)
        self.assertEqual(OperationState.FAILED, failed.status, failed)
        self.assertEqual("ROLLBACK_FAILED", failed.diagnostic_code)
        # The failed candidate was stopped and the previous version serves again.
        self.assertIn("0.1.0", self.runtime.stopped)
        await self._assert_version_serving("0.2.0", "still-v2")

    async def test_rollback_candidate_health_failure_keeps_current_version_enabled(
        self,
    ) -> None:
        await self._install_enable_and_upgrade()
        self.runtime.unhealthy_versions.add("0.1.0")
        rollback = await self.service.begin_rollback("example.echo")
        failed = await self.service.wait_operation(rollback.id)
        self.assertEqual(OperationState.FAILED, failed.status, failed)
        self.assertEqual("HEALTHCHECK_FAILED", failed.diagnostic_code)
        self.assertIn("0.1.0", self.runtime.stopped)
        await self._assert_version_serving("0.2.0", "still-v2")

    async def test_rollback_enabled_save_failure_keeps_current_version_enabled(
        self,
    ) -> None:
        self.store = FailingLifecycleStore()
        self._rebuild_service()
        await self._install_enable_and_upgrade()
        self.store.fail_next_enabled_save = True
        rollback = await self.service.begin_rollback("example.echo")
        failed = await self.service.wait_operation(rollback.id)
        self.assertEqual(OperationState.FAILED, failed.status, failed)
        self.assertEqual("ROLLBACK_FAILED", failed.diagnostic_code)
        await self._assert_version_serving("0.2.0", "still-v2")

    async def test_cancelling_rollback_restores_current_version(self) -> None:
        await self._install_enable_and_upgrade()
        self.runtime.blocked_start_versions.add("0.1.0")
        task = asyncio.create_task(self.manager.rollback("example.echo"))
        for _ in range(200):
            if "0.1.0" in self.runtime.start_blockers:
                break
            await asyncio.sleep(0.01)
        else:
            self.fail("rollback never reached the candidate start")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        # The cancelled candidate worker is gone and the original runs again.
        self.assertNotIn("0.1.0", self.runtime.active)
        self.assertEqual("0.2.0", self.runtime.active.get("example.echo"))
        await self._assert_version_serving("0.2.0", "still-v2")

    async def test_stop_all_shuts_down_workers_and_recover_restarts_enabled(
        self,
    ) -> None:
        await self._install_and_enable(EXAMPLE)
        self.assertEqual("0.1.0", self.runtime.active.get("example.echo"))
        await self.service.stop_all()
        # Every worker is gone: invocation must fail even though the persisted
        # record still says ENABLED.
        self.assertEqual({}, self.runtime.active)
        with self.assertRaises(RpcCallError):
            await self.runtime.invoke_tool(
                "example.echo",
                "example.echo",
                {"text": "after-shutdown"},
                task_id="t",
                run_id="r",
                idempotency_key="k",
                deadline_seconds=5.0,
            )
        # recover() restarts the worker from the persisted ENABLED record.
        await self.service.recover()
        self.assertEqual("0.1.0", self.runtime.active.get("example.echo"))
        await self._assert_version_serving("0.1.0", "after-recover")

    async def test_failed_activation_keeps_old_version_serving(self) -> None:
        await self._install_and_enable(EXAMPLE)
        self.operator.fail_next_activate = True
        preview = await self.service.inspect(str(_stage_copy(self.tmp, version="0.2.0")))
        upgrade = await self.service.begin_upgrade(
            "example.echo", preview.plan_id, self._confirmation(preview)
        )
        failed = await self.service.wait_operation(upgrade.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual(ExtensionState.ENABLED, record.state)
        self.assertEqual("0.1.0", record.manifest.version)
        owner = self.registry.snapshot.owner_of("example.echo")
        self.assertEqual("0.1.0", owner.extension_version if owner else None)
        result = await self.service.invoke_tool(
            "example.echo",
            "example.echo",
            {"text": "still-old"},
            task_id="t",
            run_id="r",
            idempotency_key="k",
        )
        self.assertEqual("still-old", result["output"]["echo"])
        candidate_path = str(self.tmp / "installed" / "example.echo" / "0.2.0")
        self.assertIn(candidate_path, self.installer.uninstalled)

    async def test_second_operation_for_the_same_extension_is_rejected(self) -> None:
        await self._install_and_enable(EXAMPLE)
        first = await self.service.begin_disable("example.echo")
        with self.assertRaises(ExtensionOperationError) as captured:
            await self.service.begin_disable("example.echo")
        self.assertEqual("OPERATION_IN_PROGRESS", captured.exception.code)
        final = await self.service.wait_operation(first.id)
        self.assertEqual(OperationState.SUCCEEDED, final.status, final)
        self.assertEqual(["0.1.0"], self.runtime.stopped)
        # Once the first operation finished, the extension can be operated again.
        again = await self.service.begin_enable("example.echo")
        self.assertEqual(
            OperationState.SUCCEEDED, (await self.service.wait_operation(again.id)).status
        )

    async def test_idempotent_replay_and_conflicting_command(self) -> None:
        preview = await self.service.inspect(str(EXAMPLE))
        first = await self.service.begin_install(
            preview.plan_id,
            self._confirmation(preview),
            idempotency_key="command-key",
        )
        replay = await self.service.begin_install(
            preview.plan_id,
            self._confirmation(preview),
            idempotency_key="command-key",
        )
        self.assertEqual(first.id, replay.id)
        final = await self.service.wait_operation(first.id)
        self.assertEqual(OperationState.SUCCEEDED, final.status, final)
        self.assertEqual(["0.1.0"], self.installer.installed)
        self.assertEqual([("example.echo", "0.1.0")], self.verifier.verified)

        async def noop() -> None:
            return None

        await self.service._begin(  # noqa: SLF001 - conflict path needs a raw command
            "example.echo",
            "enable",
            noop,
            request_scope="admin:extensions:example.echo:enable",
            idempotency_key="k",
            fingerprint="a" * 64,
        )
        with self.assertRaises(ExtensionOperationError) as captured:
            await self.service._begin(  # noqa: SLF001
                "example.echo",
                "enable",
                noop,
                request_scope="admin:extensions:example.echo:enable",
                idempotency_key="k",
                fingerprint="b" * 64,
            )
        self.assertEqual("IDEMPOTENCY_CONFLICT", captured.exception.code)

    async def test_upgrade_plan_rejects_a_changed_active_baseline(self) -> None:
        await self._install_and_enable(EXAMPLE)
        preview = await self.service.inspect(str(_stage_copy(self.tmp, version="0.2.0")))
        self.assertEqual("upgrade", preview.mode)
        current = await self.service.record("example.echo")
        assert current is not None
        # Simulate a concurrent activation of 0.3.0 after the 0.2.0 preview.
        await self.store.save(
            replace(current, manifest=replace(current.manifest, version="0.3.0"))
        )
        upgrade = await self.service.begin_upgrade(
            "example.echo", preview.plan_id, self._confirmation(preview)
        )
        failed = await self.service.wait_operation(upgrade.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        self.assertEqual("PLAN_BASELINE_CHANGED", failed.diagnostic_code)
        self.assertEqual(["0.1.0"], self.installer.installed)
        owner = self.registry.snapshot.owner_of("example.echo")
        self.assertEqual("0.1.0", owner.extension_version if owner else None)

    async def test_candidate_persist_failure_restores_the_old_version(self) -> None:
        await self._install_and_enable(EXAMPLE)
        flaky = FlakyLifecycleStore(
            self.store,
            predicate=lambda record: record.manifest.version == "0.2.0"
            and record.state is ExtensionState.DISABLED,
        )
        self.store = flaky
        self._rebuild_service()
        preview = await self.service.inspect(str(_stage_copy(self.tmp, version="0.2.0")))
        upgrade = await self.service.begin_upgrade(
            "example.echo", preview.plan_id, self._confirmation(preview)
        )
        failed = await self.service.wait_operation(upgrade.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        self.assertEqual(1, flaky.failures)
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual("0.1.0", record.manifest.version)
        self.assertEqual(ExtensionState.ENABLED, record.state)
        owner = self.registry.snapshot.owner_of("example.echo")
        self.assertEqual("0.1.0", owner.extension_version if owner else None)
        result = await self.service.invoke_tool(
            "example.echo",
            "example.echo",
            {"text": "still-old"},
            task_id="t",
            run_id="r",
            idempotency_key="k",
        )
        self.assertEqual("still-old", result["output"]["echo"])
        candidate_path = str(self.tmp / "installed" / "example.echo" / "0.2.0")
        self.assertIn(candidate_path, self.installer.uninstalled)

    async def _assert_failed_upgrade_keeps_the_old_version(self) -> None:
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual("0.1.0", record.manifest.version)
        self.assertEqual(ExtensionState.ENABLED, record.state)
        owner = self.registry.snapshot.owner_of("example.echo")
        self.assertEqual("0.1.0", owner.extension_version if owner else None)
        result = await self.service.invoke_tool(
            "example.echo",
            "example.echo",
            {"text": "old-version"},
            task_id="t",
            run_id="r",
            idempotency_key="k",
        )
        self.assertEqual("old-version", result["output"]["echo"])
        candidate_path = str(self.tmp / "installed" / "example.echo" / "0.2.0")
        self.assertIn(candidate_path, self.installer.uninstalled)

    async def test_upgrade_failure_on_upgrading_save_restores_old(self) -> None:
        await self._install_and_enable(EXAMPLE)
        self.store = FlakyLifecycleStore(
            self.store, predicate=lambda record: record.state is ExtensionState.UPGRADING
        )
        self._rebuild_service()
        preview = await self.service.inspect(str(_stage_copy(self.tmp, version="0.2.0")))
        upgrade = await self.service.begin_upgrade(
            "example.echo", preview.plan_id, self._confirmation(preview)
        )
        failed = await self.service.wait_operation(upgrade.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        await self._assert_failed_upgrade_keeps_the_old_version()

    async def test_upgrade_failure_on_drain_timeout_restores_old(self) -> None:
        await self._install_and_enable(EXAMPLE)
        self.runtime.fail_drain = True
        preview = await self.service.inspect(str(_stage_copy(self.tmp, version="0.2.0")))
        upgrade = await self.service.begin_upgrade(
            "example.echo", preview.plan_id, self._confirmation(preview)
        )
        failed = await self.service.wait_operation(upgrade.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        self.assertEqual("DRAIN_TIMEOUT", failed.diagnostic_code)
        await self._assert_failed_upgrade_keeps_the_old_version()

    async def test_upgrade_failure_on_candidate_start_restores_old(self) -> None:
        await self._install_and_enable(EXAMPLE)
        self.runtime.fail_start_versions = {"0.2.0"}
        preview = await self.service.inspect(str(_stage_copy(self.tmp, version="0.2.0")))
        upgrade = await self.service.begin_upgrade(
            "example.echo", preview.plan_id, self._confirmation(preview)
        )
        failed = await self.service.wait_operation(upgrade.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        await self._assert_failed_upgrade_keeps_the_old_version()

    async def test_upgrade_failure_on_candidate_enabled_save_restores_old(self) -> None:
        await self._install_and_enable(EXAMPLE)
        self.store = FlakyLifecycleStore(
            self.store,
            predicate=lambda record: record.manifest.version == "0.2.0"
            and record.state is ExtensionState.ENABLED,
        )
        self._rebuild_service()
        preview = await self.service.inspect(str(_stage_copy(self.tmp, version="0.2.0")))
        upgrade = await self.service.begin_upgrade(
            "example.echo", preview.plan_id, self._confirmation(preview)
        )
        failed = await self.service.wait_operation(upgrade.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        await self._assert_failed_upgrade_keeps_the_old_version()

    async def test_install_replay_survives_a_supervisor_restart(self) -> None:
        preview = await self.service.inspect(str(EXAMPLE))
        confirmation = self._confirmation(preview)
        first = await self.service.begin_install(
            preview.plan_id, confirmation, idempotency_key="restart-key"
        )
        self.assertEqual(
            OperationState.SUCCEEDED, (await self.service.wait_operation(first.id)).status
        )
        # Simulate a restart: a fresh coordinator has no in-memory plan at all.
        self._rebuild_service()
        replay = await self.service.begin_install(
            preview.plan_id, confirmation, idempotency_key="restart-key"
        )
        self.assertEqual(first.id, replay.id)
        self.assertEqual(["0.1.0"], self.installer.installed)
        self.assertEqual([("example.echo", "0.1.0")], self.verifier.verified)

    async def test_replay_with_a_changed_body_conflicts(self) -> None:
        preview = await self.service.inspect(str(EXAMPLE))
        confirmation = self._confirmation(preview)
        first = await self.service.begin_install(
            preview.plan_id, confirmation, idempotency_key="restart-key"
        )
        await self.service.wait_operation(first.id)
        changed = replace(confirmation, preview_hash="sha256:" + "0" * 64)
        with self.assertRaises(ExtensionOperationError) as captured:
            await self.service.begin_install(
                preview.plan_id, changed, idempotency_key="restart-key"
            )
        self.assertEqual("IDEMPOTENCY_CONFLICT", captured.exception.code)

    async def test_replay_returns_the_failed_operation_after_restart(self) -> None:
        from personal_assistant.core.extensions.operations import ExtensionOperation
        from personal_assistant.core.extensions.supervision import (
            _command_fingerprint,
            _command_material,
            _extension_request_scope,
        )

        scope = _extension_request_scope("example.echo", "disable")
        fingerprint = _command_fingerprint(
            scope, _command_material({"drain_seconds": 30.0})
        )
        await self.operations.create(
            ExtensionOperation(
                id="extop_running",
                extension_id="example.echo",
                operation="disable",
                status=OperationState.RUNNING,
                idempotency_key="restart-key",
                command_fingerprint=fingerprint,
                request_scope=scope,
            )
        )
        self._rebuild_service()
        await self.service.recover()
        replay = await self.service.begin_disable(
            "example.echo", idempotency_key="restart-key"
        )
        self.assertEqual("extop_running", replay.id)
        self.assertEqual(OperationState.FAILED, replay.status)
        self.assertEqual("SUPERVISOR_RESTART", replay.diagnostic_code)

    async def test_recovery_health_failure_stops_the_worker(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        install_path = self.tmp / "installed" / "example.echo" / "0.1.0"
        install_path.mkdir(parents=True)
        await self.store.save(
            ExtensionRecord(
                manifest=manifest,
                artifact_hash="sha256:" + "0" * 64,
                state=ExtensionState.ENABLED,
                install_path=str(install_path),
            )
        )
        self.runtime.healthy = False
        await self.service.recover()
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual(ExtensionState.QUARANTINED, record.state)
        self.assertIn("0.1.0", self.runtime.stopped)
        self.assertEqual([], list(self.registry.snapshot.capabilities))

    async def test_recovery_save_failure_stops_the_worker(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        install_path = self.tmp / "installed" / "example.echo" / "0.1.0"
        install_path.mkdir(parents=True)
        await self.store.save(
            ExtensionRecord(
                manifest=manifest,
                artifact_hash="sha256:" + "0" * 64,
                state=ExtensionState.ENABLED,
                install_path=str(install_path),
            )
        )
        flaky = FlakyLifecycleStore(
            self.store,
            predicate=lambda record: record.state is ExtensionState.ENABLED,
        )
        self.store = flaky
        self._rebuild_service()
        await self.service.recover()
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual(ExtensionState.QUARANTINED, record.state)
        self.assertIn("0.1.0", self.runtime.stopped)

    async def test_drain_report_with_active_calls_fails_the_operation(self) -> None:
        await self._install_and_enable(EXAMPLE)
        self.runtime.drain_report = {"drained": False, "active_calls": 2}
        disable = await self.service.begin_disable("example.echo")
        failed = await self.service.wait_operation(disable.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        self.assertEqual("DRAIN_TIMEOUT", failed.diagnostic_code)
        record = await self.service.record("example.echo")
        assert record is not None
        self.assertEqual(ExtensionState.DISABLED, record.state)
        self.assertEqual([], list(self.registry.snapshot.capabilities))
        self.assertEqual(["0.1.0"], self.runtime.stopped)

    async def test_running_update_failure_leaves_a_failed_operation(self) -> None:
        preview = await self.service.inspect(str(EXAMPLE))
        install = await self.service.begin_install(preview.plan_id, self._confirmation(preview))
        await self.service.wait_operation(install.id)
        flaky = FlakyOperationStore(self.operations)
        flaky.fail_next_running = True
        self.operations = flaky
        self._rebuild_service()
        operation = await self.service.begin_enable("example.echo")
        final = await self.service.wait_operation(operation.id)
        self.assertEqual(OperationState.FAILED, final.status)
        self.assertEqual("OPERATION_FAILED", final.diagnostic_code)
        self.assertEqual([], self.runtime.started)
        # The extension is not stuck: a later operation can run.
        retry = await self.service.begin_enable("example.echo")
        self.assertEqual(
            OperationState.SUCCEEDED, (await self.service.wait_operation(retry.id)).status
        )

    async def test_operation_diagnostics_stay_in_the_safe_allowlist(self) -> None:
        self.verifier.fail_next = True
        preview = await self.service.inspect(str(EXAMPLE))
        operation = await self.service.begin_install(preview.plan_id, self._confirmation(preview))
        failed = await self.service.wait_operation(operation.id)
        self.assertEqual(OperationState.FAILED, failed.status)
        assert failed.diagnostic_code is not None
        self.assertIn(failed.diagnostic_code, DIAGNOSTIC_CODES)


class InMemoryExtensionDataStoreForTests:
    async def namespaces(self, extension_id: str):
        del extension_id
        return ()

    async def purge(self, extension_id: str) -> None:
        raise ExtensionError(f"purge is not implemented for {extension_id}")


class FlakyLifecycleStore:
    """Delegates to a real store but can fail one matching save."""

    def __init__(self, inner, predicate) -> None:
        self._inner = inner
        self._predicate = predicate
        self.failures = 0

    async def get(self, extension_id: str):
        return await self._inner.get(extension_id)

    async def all(self):
        return await self._inner.all()

    async def save(self, record: ExtensionRecord) -> None:
        if self._predicate(record):
            self.failures += 1
            raise OSError("injected durable store failure")
        await self._inner.save(record)


class FlakyOperationStore:
    """Delegates to a real store but fails the RUNNING transition once."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.fail_next_running = False

    async def create(self, operation) -> None:
        await self._inner.create(operation)

    async def update(self, operation_id, *, status, diagnostic_code=None):
        if status is OperationState.RUNNING and self.fail_next_running:
            self.fail_next_running = False
            raise OSError("injected operation store failure")
        return await self._inner.update(
            operation_id, status=status, diagnostic_code=diagnostic_code
        )

    async def get(self, operation_id: str):
        return await self._inner.get(operation_id)

    async def find_by_request_scope(self, request_scope, idempotency_key):
        return await self._inner.find_by_request_scope(request_scope, idempotency_key)

    async def interrupt_running(self, *, diagnostic_code: str) -> int:
        return await self._inner.interrupt_running(diagnostic_code=diagnostic_code)


if __name__ == "__main__":
    unittest.main()
