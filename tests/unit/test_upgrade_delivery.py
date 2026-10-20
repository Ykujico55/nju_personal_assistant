from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from personal_assistant.domain import ValidationError


class UpgradePlanTests(unittest.TestCase):
    def test_exact_wheel_hashes_required_before_any_install_and_pointer_is_local(self):
        from personal_assistant.infrastructure.operations.upgrade import (
            confirm_release,
            plan_release,
            select_python,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "fixture-1.0-py3-none-any.whl"
            wheel.write_bytes(b"synthetic-wheel")
            plan = plan_release([wheel])
            self.assertEqual(
                hashlib.sha256(wheel.read_bytes()).hexdigest(), plan["wheels"][0]["sha256"]
            )
            with self.assertRaises(ValidationError):
                confirm_release(plan, "00" * 32)
            confirm_release(plan, plan["hash"])
            wheel.write_bytes(b"changed-after-preview")
            with self.assertRaises(ValidationError):
                confirm_release(plan, plan["hash"])
            with self.assertRaises(ValueError):
                select_python(root, {"python": str(Path(directory).parent / "outside.exe")})

    def test_copy_rechecks_reviewed_bytes_and_installs_from_owned_staging(self):
        from personal_assistant.infrastructure.operations.upgrade import plan_release, stage_release

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "fixture-1.0-py3-none-any.whl"
            wheel.write_bytes(b"reviewed-material")
            plan = plan_release([wheel])
            staged = stage_release(plan, plan["hash"], root / "candidate")
            wheel.write_bytes(b"changed-after-copy")
            self.assertEqual(b"reviewed-material", staged[0].read_bytes())
            with self.assertRaises(ValidationError):
                stage_release(plan, plan["hash"], root / "next-candidate")
            self.assertFalse((root / "next-candidate").exists())
