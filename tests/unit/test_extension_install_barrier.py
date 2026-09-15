from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path

from personal_assistant.core.extensions import ConfirmationRequiredError, compute_artifact_hash
from personal_assistant.core.extensions.lifecycle import (
    InstallationConfirmation,
    InstallCoordinator,
    InstalledArtifact,
    StagedArtifact,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"


class Stager:
    def __init__(self) -> None:
        self.discarded = 0

    async def stage(self, source: str) -> StagedArtifact:
        return StagedArtifact(source, EXAMPLE, compute_artifact_hash(EXAMPLE))

    async def discard(self, staged: StagedArtifact) -> None:
        del staged
        self.discarded += 1


class Installer:
    def __init__(self) -> None:
        self.execution_count = 0

    async def install(self, staged, manifest):
        del staged, manifest
        self.execution_count += 1
        return InstalledArtifact(EXAMPLE, "runtime-test")

    async def uninstall_code(self, record):
        del record

    async def clean_failed_install(self, staged):
        del staged


class Verifier:
    def __init__(self) -> None:
        self.execution_count = 0

    async def verify(self, installed, manifest):
        del installed, manifest
        self.execution_count += 1


class Store:
    def __init__(self) -> None:
        self.records = {}

    async def get(self, extension_id):
        return self.records.get(extension_id)

    async def save(self, record):
        self.records[record.manifest.id] = record


class InstallBarrierTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_build_install_import_or_worker_before_exact_confirmation(self) -> None:
        fixed = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
        installer = Installer()
        verifier = Verifier()
        coordinator = InstallCoordinator(
            Stager(), installer, verifier, Store(), clock=lambda: fixed
        )
        preview = await coordinator.prepare(str(EXAMPLE))
        self.assertEqual(0, installer.execution_count)
        self.assertEqual(0, verifier.execution_count)

        with self.assertRaises(ConfirmationRequiredError):
            await coordinator.install(preview.plan_id, None)
        self.assertEqual(0, installer.execution_count)
        self.assertEqual(0, verifier.execution_count)

        confirmed = InstallationConfirmation(
            plan_id=preview.plan_id,
            confirmation_nonce=preview.confirmation_nonce,
            preview_hash=preview.preview_hash,
            actor="local-owner",
            confirmed_at=fixed,
            accepted_warning=True,
        )
        record = await coordinator.install(preview.plan_id, confirmed)
        self.assertEqual("INSTALLED_DISABLED", record.state.value)
        self.assertEqual(1, installer.execution_count)
        self.assertEqual(1, verifier.execution_count)


if __name__ == "__main__":
    unittest.main()

