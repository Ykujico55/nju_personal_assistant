"""F02: the exact-confirmation barrier for install and side-by-side upgrade."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_assistant.core.extensions import (
    ConfirmationRequiredError,
    ExtensionRecord,
    ExtensionState,
    ManifestParser,
    compute_artifact_hash,
)
from personal_assistant.core.extensions.errors import ExtensionError
from personal_assistant.core.extensions.lifecycle import (
    InstallationConfirmation,
    InstallCoordinator,
    InstalledArtifact,
    StagedArtifact,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"


def _safe_rmtree(path: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise AssertionError(f"refusing to delete {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _stage_copy(root: Path, *, version: str | None = None, schema: int | None = None) -> Path:
    destination = root / f"artifact-{version or 'base'}-{schema or 'base'}"
    shutil.copytree(EXAMPLE, destination)
    manifest_path = destination / "extension.toml"
    text = manifest_path.read_text("utf-8")
    if version is not None:
        text = text.replace('version = "0.1.0"', f'version = "{version}"')
    if schema is not None:
        text = text.replace("state_schema_version = 1", f"state_schema_version = {schema}")
    manifest_path.write_text(text, encoding="utf-8")
    return destination


class CopyingStager:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.discarded = 0

    async def stage(self, source: str) -> StagedArtifact:
        target = self._root / f"staged-{len(list(self._root.iterdir()))}"
        shutil.copytree(Path(source), target)
        return StagedArtifact(str(source), target, compute_artifact_hash(target))

    async def discard(self, staged: StagedArtifact) -> None:
        del staged
        self.discarded += 1


class SpyInstaller:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.executions = 0
        self.cleaned = 0
        self.removed: list[str] = []

    async def install(self, staged: StagedArtifact, manifest) -> InstalledArtifact:
        self.executions += 1
        target = self._root / "installed" / manifest.version
        (target / "payload").mkdir(parents=True, exist_ok=True)
        (target / "venv").mkdir(parents=True, exist_ok=True)
        return InstalledArtifact(target, "runtime-test")

    async def uninstall_code(self, record: ExtensionRecord) -> None:
        del record

    async def remove_version(self, record: ExtensionRecord) -> None:
        if record.install_path:
            self.removed.append(record.install_path)
            shutil.rmtree(Path(record.install_path), ignore_errors=True)

    async def clean_failed_install(self, staged: StagedArtifact) -> None:
        del staged
        self.cleaned += 1


class SpyVerifier:
    def __init__(self) -> None:
        self.executions = 0

    async def verify(self, installed, manifest) -> None:
        del installed, manifest
        self.executions += 1


class Store:
    def __init__(self) -> None:
        self.records: dict[str, ExtensionRecord] = {}

    async def get(self, extension_id: str):
        return self.records.get(extension_id)

    async def all(self) -> tuple[ExtensionRecord, ...]:
        return tuple(self.records.values())

    async def save(self, record: ExtensionRecord) -> None:
        self.records[record.manifest.id] = record


class FlakyStore(Store):
    """Fails the final INSTALLED_DISABLED save exactly once."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_final_save = True

    async def save(self, record: ExtensionRecord) -> None:
        if record.state is ExtensionState.INSTALLED_DISABLED and self.fail_final_save:
            self.fail_final_save = False
            raise OSError("injected final persistence failure")
        await super().save(record)


class InstallBarrierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f02_barrier_"))
        self.stager = CopyingStager(self.tmp)
        self.installer = SpyInstaller(self.tmp)
        self.verifier = SpyVerifier()
        self.store = Store()
        self.now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        self.coordinator = InstallCoordinator(
            self.stager,
            self.installer,
            self.verifier,
            self.store,
            clock=lambda: self.now,
        )

    def tearDown(self) -> None:
        _safe_rmtree(self.tmp)

    def _confirmation(self, preview, **overrides) -> InstallationConfirmation:
        values = {
            "plan_id": preview.plan_id,
            "confirmation_nonce": preview.confirmation_nonce,
            "preview_hash": preview.preview_hash,
            "actor": "local-owner",
            "confirmed_at": self.now,
            "accepted_warning": True,
        }
        values.update(overrides)
        return InstallationConfirmation(**values)

    async def test_confirmation_is_required_before_any_execution(self) -> None:
        preview = await self.coordinator.prepare(str(EXAMPLE))
        self.assertEqual("install", preview.mode)
        self.assertIsNone(preview.replaces_version)
        self.assertEqual(0, self.installer.executions)
        self.assertEqual(0, self.verifier.executions)
        self.assertEqual(ExtensionState.STAGED, self.store.records["example.echo"].state)

        for confirmation in (
            None,
            self._confirmation(preview, accepted_warning=False),
            self._confirmation(preview, confirmation_nonce="wrong"),
            self._confirmation(preview, preview_hash="sha256:" + "0" * 64),
            self._confirmation(preview, confirmed_at=self.now + timedelta(minutes=5)),
        ):
            with self.subTest(confirmation=confirmation):
                with self.assertRaises(ConfirmationRequiredError):
                    await self.coordinator.install(preview.plan_id, confirmation)
                self.assertEqual(0, self.installer.executions)
                self.assertEqual(0, self.verifier.executions)

        record = await self.coordinator.install(preview.plan_id, self._confirmation(preview))
        self.assertEqual(ExtensionState.INSTALLED_DISABLED, record.state)
        self.assertEqual(1, self.installer.executions)
        self.assertEqual(1, self.verifier.executions)

    async def test_changed_staged_artifact_invalidates_the_confirmation(self) -> None:
        preview = await self.coordinator.prepare(str(EXAMPLE))
        staged_root = self.stager._root / "staged-0"
        (staged_root / "extension.toml").write_text(
            'id = "example.echo"\n', encoding="utf-8"
        )
        with self.assertRaises(ConfirmationRequiredError):
            await self.coordinator.install(preview.plan_id, self._confirmation(preview))
        self.assertEqual(0, self.installer.executions)
        self.assertEqual(0, self.verifier.executions)

    async def test_reject_discards_staging_without_execution(self) -> None:
        preview = await self.coordinator.prepare(str(EXAMPLE))
        record = await self.coordinator.reject(preview.plan_id)
        self.assertEqual(ExtensionState.REJECTED, record.state)
        self.assertEqual(1, self.stager.discarded)
        self.assertEqual(0, self.installer.executions)

    async def test_upgrade_preparation_keeps_the_active_record_untouched(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        active = ExtensionRecord(
            manifest=manifest,
            artifact_hash=compute_artifact_hash(EXAMPLE),
            state=ExtensionState.ENABLED,
            install_path=str(self.tmp / "installed" / "0.1.0"),
        )
        self.store.records[manifest.id] = active

        preview = await self.coordinator.prepare_upgrade(
            str(_stage_copy(self.tmp, version="0.2.0"))
        )
        self.assertEqual("upgrade", preview.mode)
        self.assertEqual("0.2.0", preview.extension_version)
        self.assertEqual("0.1.0", preview.replaces_version)
        self.assertEqual(ExtensionState.ENABLED, self.store.records[manifest.id].state)
        self.assertEqual(0, self.installer.executions)

        candidate = await self.coordinator.install_candidate(
            preview.plan_id, self._confirmation(preview)
        )
        self.assertEqual(ExtensionState.INSTALLED_DISABLED, candidate.state)
        self.assertEqual("0.2.0", candidate.manifest.version)
        # The candidate is never persisted before activation.
        self.assertEqual(ExtensionState.ENABLED, self.store.records[manifest.id].state)
        self.assertEqual(1, self.installer.executions)
        self.assertEqual(1, self.verifier.executions)

    async def test_upgrade_requires_a_newer_compatible_version(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        self.store.records[manifest.id] = ExtensionRecord(
            manifest=manifest,
            artifact_hash=compute_artifact_hash(EXAMPLE),
            state=ExtensionState.DISABLED,
        )
        with self.assertRaises(ExtensionError):
            await self.coordinator.prepare_upgrade(str(EXAMPLE))
        with self.assertRaises(ExtensionError):
            await self.coordinator.prepare_upgrade(
                str(_stage_copy(self.tmp, version="0.2.0", schema=0))
            )

    async def test_rejecting_an_upgrade_plan_keeps_the_active_record(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        active = ExtensionRecord(
            manifest=manifest,
            artifact_hash=compute_artifact_hash(EXAMPLE),
            state=ExtensionState.ENABLED,
            install_path=str(self.tmp / "installed" / "0.1.0"),
        )
        self.store.records[manifest.id] = active

        preview = await self.coordinator.prepare_upgrade(
            str(_stage_copy(self.tmp, version="0.2.0"))
        )
        rejected = await self.coordinator.reject(preview.plan_id)
        self.assertEqual(ExtensionState.ENABLED, rejected.state)
        self.assertEqual("0.1.0", rejected.manifest.version)
        self.assertEqual(active, self.store.records[manifest.id])
        self.assertEqual(1, self.stager.discarded)

    async def test_rejected_or_stale_install_plans_can_be_prepared_again(self) -> None:
        preview = await self.coordinator.prepare(str(EXAMPLE))
        rejected = await self.coordinator.reject(preview.plan_id)
        self.assertEqual(ExtensionState.REJECTED, rejected.state)

        fresh = await self.coordinator.prepare(str(EXAMPLE))
        record = await self.coordinator.install(fresh.plan_id, self._confirmation(fresh))
        self.assertEqual(ExtensionState.INSTALLED_DISABLED, record.state)

    async def test_a_new_preview_supersedes_an_unconfirmed_plan(self) -> None:
        first = await self.coordinator.prepare(str(EXAMPLE))
        second = await self.coordinator.prepare(str(EXAMPLE))
        self.assertNotEqual(first.plan_id, second.plan_id)
        with self.assertRaises(ConfirmationRequiredError):
            await self.coordinator.install(first.plan_id, self._confirmation(first))
        record = await self.coordinator.install(
            second.plan_id, self._confirmation(second)
        )
        self.assertEqual(ExtensionState.INSTALLED_DISABLED, record.state)

    async def test_final_persistence_failure_removes_unregistered_code(self) -> None:
        flaky = FlakyStore()
        coordinator = InstallCoordinator(
            self.stager,
            self.installer,
            self.verifier,
            flaky,
            clock=lambda: self.now,
        )
        preview = await coordinator.prepare(str(EXAMPLE))
        with self.assertRaises(OSError):
            await coordinator.install(preview.plan_id, self._confirmation(preview))
        self.assertEqual(1, self.installer.executions)
        self.assertEqual(str(self.tmp / "installed" / "0.1.0"), self.installer.removed[0])
        stored = flaky.records["example.echo"]
        self.assertEqual(ExtensionState.REJECTED, stored.state)
        self.assertIsNone(stored.install_path)

    async def test_install_and_upgrade_plans_cannot_be_swapped(self) -> None:
        install_preview = await self.coordinator.prepare(str(EXAMPLE))
        with self.assertRaises(ExtensionError):
            await self.coordinator.install_candidate(
                install_preview.plan_id, self._confirmation(install_preview)
            )

        manifest = ManifestParser().parse(EXAMPLE)
        self.store.records[manifest.id] = ExtensionRecord(
            manifest=manifest,
            artifact_hash=compute_artifact_hash(EXAMPLE),
            state=ExtensionState.DISABLED,
        )
        upgrade_preview = await self.coordinator.prepare_upgrade(
            str(_stage_copy(self.tmp, version="0.2.0"))
        )
        with self.assertRaises(ExtensionError):
            await self.coordinator.install(
                upgrade_preview.plan_id, self._confirmation(upgrade_preview)
            )
        self.assertEqual(0, self.installer.executions)


class ConfirmationBindingTests(unittest.TestCase):
    def test_replaces_version_is_bound_into_the_preview_hash(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        staged = StagedArtifact("source", manifest.root, compute_artifact_hash(EXAMPLE))
        from personal_assistant.core.extensions.lifecycle import _make_install_preview

        base = _make_install_preview(staged, manifest, expires_at=datetime.now(UTC))
        upgraded = _make_install_preview(
            staged,
            replace(manifest, version="0.2.0"),
            expires_at=datetime.now(UTC),
            mode="upgrade",
            replaces_version="0.1.0",
        )
        self.assertNotEqual(base.preview_hash, upgraded.preview_hash)
        self.assertEqual("upgrade", upgraded.mode)


if __name__ == "__main__":
    unittest.main()
