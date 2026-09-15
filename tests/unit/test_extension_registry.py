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

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"


class ExtensionRegistryTests(unittest.TestCase):
    def test_example_manifest_is_static_and_registers_all_slots(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        self.assertEqual("example.echo", manifest.id)
        self.assertEqual(
            {"ToolProvider", "ContextProvider", "ScheduleProvider", "FormSchemaProvider"},
            set(manifest.slots),
        )
        self.assertTrue(compute_artifact_hash(EXAMPLE).startswith("sha256:"))

        registry = ExtensionRegistry()
        snapshot = registry.enable(
            ExtensionRecord(
                manifest=manifest,
                artifact_hash=compute_artifact_hash(EXAMPLE),
                state=ExtensionState.ENABLED,
            )
        )
        owner = snapshot.owner_of("example.echo")
        self.assertIsNotNone(owner)
        self.assertEqual("example.echo", owner.extension_id if owner else None)
        self.assertIsNone(registry.disable("example.echo").owner_of("example.echo"))


if __name__ == "__main__":
    unittest.main()

