from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugin_developer.control import ControlError, ControlStore
from plugin_developer.validation import candidate_digest


class FixtureWorkspace:
    def __init__(self, root):
        self.root = root
        self.candidate = root / "candidates" / "fixture"
        self.candidate.mkdir(parents=True)
        (self.candidate / "extension.toml").write_text(
            'id = "fixture.plugin"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (self.candidate / "requirements.lock").write_text("", encoding="utf-8")
        (self.candidate / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (self.candidate / "code.py").write_text("VALUE = 1\n", encoding="utf-8")

    def candidate_path(self, identifier):
        if identifier != "fixture":
            raise ValueError("unknown candidate")
        return self.candidate


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = FixtureWorkspace(Path(self.temp.name))
        self.control = ControlStore(self.workspace)
        self.digest = candidate_digest(self.workspace.candidate)

    def passed(self):
        return {
            "status": "PASSED", "candidate_hash": self.digest,
            "tests_run": 1, "failures": 0, "errors": 0, "skipped": 0, "exit_code": 0,
        }

    def authorize(self, key="key", digest=None):
        return self.control.authorize_run("fixture", digest or self.digest, key)

    def finish(self, request, result=None, key="key", digest=None):
        return self.control.record_result(
            "fixture", digest or self.digest, key, request["run_token"], result or self.passed()
        )

    def test_no_grant_cannot_execute(self):
        with self.assertRaises(ControlError):
            self.authorize()

    def test_exact_authorization_cached_replay_and_budget_are_persistent(self):
        self.control.grant("fixture", self.digest)
        request = self.authorize()
        self.assertEqual("AUTHORIZED", request["state"])
        self.finish(request)
        again = ControlStore(self.workspace)
        replay = again.authorize_run("fixture", self.digest, "key")
        self.assertEqual("CACHED", replay["state"])
        self.assertEqual("PASSED", replay["result"]["status"])
        self.assertEqual("PASSED", again.latest_pass("fixture", self.digest)["status"])
        with self.assertRaises(ControlError):
            again.authorize_run("fixture", self.digest, "new-key")

    def test_changed_source_invalidates_exact_grant(self):
        self.control.grant("fixture", self.digest)
        (self.workspace.candidate / "code.py").write_text("VALUE = 2\n", encoding="utf-8")
        changed = candidate_digest(self.workspace.candidate)
        with self.assertRaises(ControlError):
            self.authorize(digest=changed)
        with self.assertRaises(ControlError):
            self.authorize()

    def test_expired_grant_cannot_start(self):
        with patch("plugin_developer.control.time.time", return_value=100):
            self.control.grant("fixture", self.digest, ttl_seconds=1)
        with (
            patch("plugin_developer.control.time.time", return_value=102),
            self.assertRaises(ControlError),
        ):
            self.authorize()

    def test_iterative_grant_allows_code_edits_but_not_manifest_changes(self):
        self.control.grant("fixture", self.digest, allow_edits=True, max_runs=5)
        first = self.authorize()
        self.finish(first, {**self.passed(), "status": "FAILED", "failures": 1, "exit_code": 1})
        (self.workspace.candidate / "code.py").write_text("VALUE = 2\n", encoding="utf-8")
        changed = candidate_digest(self.workspace.candidate)
        second = self.authorize("key-2", changed)
        self.assertEqual("AUTHORIZED", second["state"])
        self.finish(second, {**self.passed(), "candidate_hash": changed}, "key-2", changed)
        (self.workspace.candidate / "extension.toml").write_text(
            'id = "fixture.plugin"\nversion = "0.2.0"\n', encoding="utf-8"
        )
        with self.assertRaises(ControlError):
            self.authorize("key-3", candidate_digest(self.workspace.candidate))

    def test_same_request_key_cannot_change_material(self):
        self.control.grant("fixture", self.digest, allow_edits=True, max_runs=5)
        request = self.authorize()
        self.finish(request)
        (self.workspace.candidate / "code.py").write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaises(ControlError):
            self.authorize("key", candidate_digest(self.workspace.candidate))

    def test_running_or_unknown_cannot_be_replayed_under_new_key(self):
        self.control.grant("fixture", self.digest, allow_edits=True, max_runs=5)
        request = self.authorize()
        with self.assertRaises(ControlError):
            self.authorize()
        with self.assertRaises(ControlError):
            self.authorize("new-key")
        self.finish(request, {"status": "UNKNOWN", "candidate_hash": self.digest})
        with self.assertRaises(ControlError):
            self.authorize("new-key")
        with self.assertRaises(ControlError):
            self.control.grant("fixture", self.digest)

    def test_bad_pass_receipt_becomes_unknown_and_never_enables_install(self):
        self.control.grant("fixture", self.digest)
        request = self.authorize()
        with self.assertRaises(ControlError):
            self.finish(request, {**self.passed(), "tests_run": 0})
        self.assertIsNone(self.control.latest_pass("fixture", self.digest))
        self.assertEqual("UNKNOWN", self.control.status("fixture")["runs"][0]["status"])
        with self.assertRaises(ControlError):
            self.authorize()

    def test_status_exposes_neither_run_token_nor_control_path(self):
        self.control.grant("fixture", self.digest)
        request = self.authorize()
        status = str(self.control.status("fixture"))
        self.assertNotIn(request["run_token"], status)
        self.assertNotIn(str(self.workspace.root), status)
        self.assertNotIn("control.sqlite3", status)

    def test_grants_are_bounded(self):
        for kwargs in (
            {"ttl_seconds": 0}, {"ttl_seconds": 1801, "allow_edits": True},
            {"allow_edits": True, "max_runs": 6}, {"max_runs": 2},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ControlError):
                self.control.grant("fixture", self.digest, **kwargs)

    def test_later_failure_or_running_result_invalidates_earlier_pass(self):
        self.control.grant("fixture", self.digest)
        first = self.authorize()
        self.finish(first)
        self.control.grant("fixture", self.digest)
        second = self.authorize("second")
        self.assertIsNone(self.control.latest_pass("fixture", self.digest))
        self.finish(
            second, {**self.passed(), "status": "FAILED", "failures": 1, "exit_code": 1},
            key="second",
        )
        self.assertIsNone(self.control.latest_pass("fixture", self.digest))

    def test_other_snapshot_running_or_unknown_blocks_old_pass_after_edit_back(self):
        self.control.grant("fixture", self.digest, allow_edits=True, max_runs=5)
        first = self.authorize()
        self.finish(first)
        code = self.workspace.candidate / "code.py"
        code.write_text("VALUE = 2\n", encoding="utf-8")
        changed = candidate_digest(self.workspace.candidate)
        second = self.authorize("second", changed)
        code.write_text("VALUE = 1\n", encoding="utf-8")
        self.assertIsNone(self.control.latest_pass("fixture", self.digest))
        self.finish(second, {"status": "UNKNOWN", "candidate_hash": changed},
                    key="second", digest=changed)
        self.assertIsNone(self.control.latest_pass("fixture", self.digest))
