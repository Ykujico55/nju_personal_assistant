from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from personal_assistant.cli.scaffold import scaffold_extension
from personal_assistant.core.extensions import ManifestParser


class ExtensionScaffoldTests(unittest.TestCase):
    def test_generated_extension_is_immediately_manifest_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "demo"
            files = scaffold_extension("demo.weather", target)
            self.assertGreaterEqual(len(files), 7)
            manifest = ManifestParser().parse(target)
            self.assertEqual("demo.weather", manifest.id)
            self.assertEqual(("demo.weather.ping",), manifest.capability_ids)
            worker = (target / "src" / "demo_weather" / "worker.py").read_text("utf-8")
            self.assertNotIn("personal_assistant.core", worker)


if __name__ == "__main__":
    unittest.main()

