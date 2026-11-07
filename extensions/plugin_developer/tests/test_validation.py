"""Validation uses synthetic trusted candidates, never the user's live plugins."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from plugin_developer.validation import (
    ValidationError,
    candidate_digest,
    candidate_files,
    run_validation,
    snapshot_candidate,
    static_review,
)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.candidate = self.base / "candidate"
        self.candidate.mkdir()
        self.write(
            "extension.toml",
            (
                'manifest_version = "1"\nid = "fixture.example"\nname = "Fixture"\n'
                'version = "0.1.0"\ncore_api = ">=1,<2"\npython = ">=3.12,<3.14"\n'
                'entrypoint = "fixture_example.worker:create_extension"\n'
                'dependency_lock = "requirements.lock"\nstate_schema_version = 1\n'
                'healthcheck = "system.health"\n[capabilities]\nrequired = []\noptional = []\n'
            ),
        )
        self.write(
            "pyproject.toml",
            (
                '[project]\nname = "fixture-example"\nversion = "0.1.0"\n'
                'requires-python = ">=3.12"\ndependencies = []\n'
            ),
        )
        self.write("requirements.lock", "# no third party\n")
        self.write("src/fixture_example/worker.py", "VALUE = 1\n")
        self.write(
            "tests/test_example.py",
            (
                "import unittest\nclass Example(unittest.TestCase):\n"
                "    def test_value(self):\n        self.assertEqual(1, 1)\n"
            ),
        )

    def write(self, relative, content):
        path = self.candidate / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def run_current(self, **options):
        digest = candidate_digest(self.candidate)
        return run_validation(
            self.candidate, expected_digest=digest, approved_digest=digest, **options
        )

    def test_digest_pins_the_complete_text_material_and_snapshot(self):
        first = candidate_digest(self.candidate)
        self.assertEqual(first, candidate_digest(self.candidate))
        files = candidate_files(self.candidate)
        self.assertEqual(sorted(item["path"] for item in files), [item["path"] for item in files])
        target = self.base / "snapshot"
        self.assertEqual(target, snapshot_candidate(self.candidate, target, first))
        self.assertEqual(first, candidate_digest(target))
        self.write("README.md", "Changed public documentation")
        self.assertNotEqual(first, candidate_digest(self.candidate))
        with self.assertRaises(ValidationError):
            snapshot_candidate(self.candidate, self.base / "stale", first)
        self.assertFalse((self.base / "stale").exists())

    def test_invalid_material_never_enters_review_or_execution(self):
        for relative, content in (
            (".private.txt", "public fixture"),
            ("payload.exe", "not a binary"),
            ("oversize.txt", "x" * (512 * 1024 + 1)),
        ):
            with self.subTest(path=relative):
                self.write(relative, content)
                with self.assertRaises(ValidationError):
                    candidate_digest(self.candidate)
                (self.candidate / relative).unlink()

    def test_static_review_parses_without_importing_generated_code(self):
        marker = self.base / "must-not-exist"
        self.write("src/fixture_example/worker.py", f"open({str(marker)!r}, 'w').write('bad')\n")
        review = static_review(self.candidate)
        self.assertTrue(review["ok"], review)
        self.assertEqual("fixture.example", review["manifest"]["id"])
        self.assertFalse(marker.exists())

    def test_syntax_and_unpinned_dependencies_have_bounded_safe_diagnostics(self):
        self.write("src/fixture_example/worker.py", "PRIVATE_SOURCE = (\n")
        self.write("requirements.lock", "unsafe-package>=1\n")
        review = static_review(self.candidate)
        self.assertFalse(review["ok"])
        self.assertNotIn("PRIVATE_SOURCE", json.dumps(review))
        self.assertNotIn(str(self.candidate), json.dumps(review))
        self.assertTrue(any(item["code"] == "PYTHON_SYNTAX_INVALID" for item in review["errors"]))
        self.assertTrue(any(item["code"] == "DEPENDENCY_LOCK_INVALID" for item in review["errors"]))

    def test_no_or_stale_approval_cannot_create_a_process(self):
        digest = candidate_digest(self.candidate)
        for approved in ("", "0" * 64):
            with self.subTest(approved=approved), patch("subprocess.Popen") as launch:
                result = run_validation(
                    self.candidate, expected_digest=digest, approved_digest=approved
                )
                self.assertEqual("BLOCKED", result["status"])
                launch.assert_not_called()
        self.write("README.md", "new material")
        with patch("subprocess.Popen") as launch:
            result = run_validation(self.candidate, expected_digest=digest, approved_digest=digest)
            self.assertEqual("BLOCKED", result["status"])
            launch.assert_not_called()

    def test_real_validation_runs_snapshot_and_scrubs_host_settings(self):
        self.write(
            "tests/test_example.py",
            (
                "import os, unittest\nclass Example(unittest.TestCase):\n"
                "    def test_environment(self):\n"
                "        self.assertNotIn('PA_PUBLIC_FIXTURE', os.environ)\n"
                "        self.assertNotIn('DATABASE_FIXTURE', os.environ)\n"
                "        self.assertNotIn('HTTP_PROXY', os.environ)\n"
                f"        self.assertNotEqual({str(self.candidate)!r}, os.getcwd())\n"
            ),
        )
        before = candidate_digest(self.candidate)
        with patch.dict(
            os.environ,
            {
                "PA_PUBLIC_FIXTURE": "PUBLIC",
                "DATABASE_FIXTURE": "PUBLIC",
                "HTTP_PROXY": "http://127.0.0.1:9",
            },
        ):
            result = self.run_current()
        self.assertEqual("PASSED", result["status"], result)
        self.assertEqual(1, result["tests_run"])
        self.assertEqual(before, candidate_digest(self.candidate))
        self.assertNotIn(str(self.candidate), json.dumps(result))
        self.assertEqual([], list(self.candidate.rglob("__pycache__")))

    def test_failure_returns_source_location_but_no_output_or_exception_message(self):
        self.write(
            "tests/test_example.py",
            (
                "import unittest\nclass Example(unittest.TestCase):\n"
                "    def test_failure(self):\n"
                "        print('PRIVATE_STDOUT')\n"
                "        raise ValueError('PRIVATE_ERROR_BODY')\n"
            ),
        )
        result = self.run_current(log_directory=self.base / "logs")
        self.assertEqual("FAILED", result["status"], result)
        encoded = json.dumps(result)
        self.assertNotIn("PRIVATE_STDOUT", encoded)
        self.assertNotIn("PRIVATE_ERROR_BODY", encoded)
        self.assertNotIn(str(self.base), encoded)
        self.assertEqual("ValueError", result["failed_tests"][0]["exception_type"])
        self.assertEqual("tests/test_example.py", result["failed_tests"][0]["relative_path"])
        self.assertEqual(5, result["failed_tests"][0]["line"])
        self.assertTrue((self.base / "logs" / result["log_name"]).is_file())

    def test_zero_discovered_tests_never_pass(self):
        self.write("tests/test_example.py", "VALUE = 1\n")
        result = self.run_current()
        self.assertEqual("FAILED", result["status"], result)
        self.assertEqual(0, result["tests_run"])

    def test_timeout_is_bounded_and_kills_descendant_processes(self):
        marker = self.base / "late-child-must-not-write"
        child = f"import time; time.sleep(1.8); open({str(marker)!r}, 'w').write('late')"
        self.write(
            "tests/test_example.py",
            (
                "import subprocess, sys, time, unittest\nclass Example(unittest.TestCase):\n"
                "    def test_timeout(self):\n"
                f"        subprocess.Popen([sys.executable, '-c', {child!r}])\n"
                "        time.sleep(10)\n"
            ),
        )
        started = time.monotonic()
        result = self.run_current(timeout_seconds=0.4)
        self.assertEqual("TIMED_OUT", result["status"], result)
        self.assertLess(time.monotonic() - started, 5)
        time.sleep(2)
        self.assertFalse(marker.exists())

    def test_output_is_capped_without_being_returned_to_the_agent(self):
        self.write(
            "tests/test_example.py",
            (
                "import unittest\nclass Example(unittest.TestCase):\n"
                "    def test_output(self):\n        print('x' * 300000)\n"
            ),
        )
        result = self.run_current(log_directory=self.base / "logs")
        self.assertEqual("PASSED", result["status"], result)
        self.assertTrue(result["stdout_truncated"])
        self.assertLess((self.base / "logs" / result["log_name"]).stat().st_size, 140000)

    def test_cancellation_reaps_the_running_candidate(self):
        self.write(
            "tests/test_example.py",
            (
                "import time, unittest\nclass Example(unittest.TestCase):\n"
                "    def test_wait(self):\n        time.sleep(10)\n"
            ),
        )
        event = threading.Event()
        timer = threading.Timer(0.4, event.set)
        timer.start()
        try:
            result = self.run_current(cancel_event=event)
        finally:
            timer.cancel()
        self.assertEqual("CANCELLED", result["status"], result)
        self.assertLess(result["duration_seconds"], 5)

    def test_private_core_import_rejected_but_public_sdk_import_allowed(self):
        self.write("src/fixture_example/worker.py", "import personal_assistant_sdk\n")
        self.assertTrue(static_review(self.candidate)["ok"])
        self.write("src/fixture_example/worker.py", "import personal_assistant.core\n")
        result = static_review(self.candidate)
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["code"] == "PRIVATE_HOST_IMPORT" for item in result["errors"]))

    def test_all_skipped_and_missing_lock_never_pass(self):
        self.write(
            "tests/test_example.py",
            (
                "import unittest\nclass Example(unittest.TestCase):\n"
                "    @unittest.skip('fixture')\n"
                "    def test_skip(self):\n        pass\n"
            ),
        )
        result = self.run_current()
        self.assertEqual("FAILED", result["status"], result)
        self.assertEqual(1, result["skipped"])
        (self.candidate / "requirements.lock").unlink()
        self.assertFalse(static_review(self.candidate)["ok"])

    def test_change_after_review_cannot_change_executed_snapshot(self):
        digest = candidate_digest(self.candidate)
        original_review = static_review

        def changed(root):
            result = original_review(root)
            self.write("README.md", "changed-after-review")
            return result

        with (
            patch("plugin_developer.validation.static_review", side_effect=changed),
            patch("subprocess.Popen") as launch,
        ):
            result = run_validation(self.candidate, expected_digest=digest, approved_digest=digest)
        self.assertEqual("BLOCKED", result["status"], result)
        launch.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows native cleanup")
    def test_cleanup_failure_cannot_be_reported_as_success(self):
        from plugin_developer.validation import _WindowsJob

        original_close = _WindowsJob.close

        def fail_after_real_close(job):
            original_close(job)
            raise ValidationError("INJECTED_CLEANUP_UNCONFIRMED")

        with patch.object(_WindowsJob, "close", fail_after_real_close):
            result = self.run_current()
        self.assertEqual("UNKNOWN", result["status"], result)
        self.assertEqual("PROCESS_CLEANUP_UNCONFIRMED", result["code"])

    @unittest.skipUnless(os.name == "nt", "Windows native process assignment")
    def test_job_assignment_failure_never_starts_candidate_code(self):
        from plugin_developer.validation import _WindowsJob

        marker = self.base / "must-not-run"
        self.write("tests/test_example.py", f"open({str(marker)!r}, 'w').write('bad')\n")
        with patch.object(
            _WindowsJob, "assign", side_effect=ValidationError("PROCESS_CONTAINMENT_UNAVAILABLE")
        ):
            result = self.run_current()
        self.assertEqual("BLOCKED", result["status"], result)
        self.assertFalse(marker.exists())
