from __future__ import annotations

import unittest
from pathlib import Path

from personal_assistant.core.extensions import (
    ExtensionRecord,
    ExtensionRegistry,
    ExtensionState,
    ManifestParser,
    compute_artifact_hash,
)
from personal_assistant.core.extensions.lifecycle import LifecycleManager

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"


class Store:
    def __init__(self, record: ExtensionRecord) -> None:
        self.record = record
        self.fail_enabled_once = True

    async def get(self, extension_id: str):
        return self.record if extension_id == self.record.manifest.id else None

    async def save(self, record: ExtensionRecord) -> None:
        if record.state is ExtensionState.ENABLED and self.fail_enabled_once:
            self.fail_enabled_once = False
            raise OSError("simulated durable store failure")
        self.record = record


class Supervisor:
    def __init__(self) -> None:
        self.stopped = False

    async def start(self, record):
        del record

    async def health(self, record):
        del record
        return True

    async def drain(self, record, deadline_epoch):
        del record, deadline_epoch

    async def stop(self, record):
        del record
        self.stopped = True


class NoopInstaller:
    async def install(self, staged, manifest):
        raise AssertionError("not used")

    async def uninstall_code(self, record):
        del record

    async def clean_failed_install(self, staged):
        del staged


class NoopData:
    async def namespaces(self, extension_id):
        del extension_id
        return ()

    async def purge(self, extension_id):
        del extension_id


class NoopVersions:
    async def activate(self, old, candidate):
        del old, candidate

    async def rollback(self, current):
        return current


class ExtensionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_enable_persistence_failure_revokes_published_capabilities(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        initial = ExtensionRecord(
            manifest=manifest,
            artifact_hash=compute_artifact_hash(EXAMPLE),
            state=ExtensionState.INSTALLED_DISABLED,
        )
        store = Store(initial)
        registry = ExtensionRegistry()
        supervisor = Supervisor()
        manager = LifecycleManager(
            store,
            registry,
            supervisor,
            NoopInstaller(),
            NoopData(),
            NoopVersions(),
        )
        with self.assertRaises(OSError):
            await manager.enable(manifest.id)
        self.assertIsNone(registry.snapshot.owner_of("example.echo"))
        self.assertEqual(ExtensionState.QUARANTINED, store.record.state)
        self.assertTrue(supervisor.stopped)


if __name__ == "__main__":
    unittest.main()

