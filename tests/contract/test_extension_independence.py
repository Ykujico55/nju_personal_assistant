from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ExtensionIndependenceContract(unittest.TestCase):
    def test_core_has_no_example_extension_special_case(self) -> None:
        offenders = []
        for path in (ROOT / "src" / "personal_assistant" / "core").rglob("*.py"):
            if "example.echo" in path.read_text("utf-8"):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_business_extension_imports_only_public_sdk(self) -> None:
        offenders = []
        for path in (ROOT / "extensions" / "example_echo").rglob("*.py"):
            text = path.read_text("utf-8")
            if "personal_assistant.core" in text or "personal_assistant.infrastructure" in text:
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()

