from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from plugin_developer import cli


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.digest = "sha256:" + "a" * 64
        self.review = {
            "candidate_hash": self.digest, "extension_id": "fixture.plugin",
            "version": "0.1.0", "files": [], "diff": {}, "manifest": {},
        }
        self.workspace = MagicMock()
        self.workspace.root = self.root
        self.workspace.review.return_value = self.review
        self.workspace.candidate_path.return_value = self.root
        self.workspace.export.return_value = self.root / "exported"
        self.control = MagicMock()
        self.control.grant.return_value = {"candidate_hash": self.digest}
        self.control.latest_pass.return_value = {
            "status": "PASSED", "candidate_hash": self.digest, "tests_run": 1,
        }
        self.real_terminal = cli._require_terminal
        self.patches = [
            patch.object(cli, "Workspace", return_value=self.workspace),
            patch.object(cli, "ControlStore", return_value=self.control),
            patch.object(cli, "_require_terminal"),
            patch.object(cli, "static_review", return_value={"ok": True}),
            patch.object(cli, "candidate_digest", return_value=self.digest),
            patch("builtins.input", return_value="YES"),
            patch.object(cli.subprocess, "run"),
        ]
        self.mocks = [item.start() for item in self.patches]
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])
        self.mocks[-1].return_value.returncode = 0

    def call(self, *args):
        return cli.main(["--workspace-root", str(self.root), *args])

    def test_exact_approve_is_local_only_and_does_not_execute(self):
        self.assertEqual(0, self.call("approve-tests", "fixture"))
        self.control.grant.assert_called_once_with(
            "fixture", self.digest, ttl_seconds=900, allow_edits=False, max_runs=1
        )
        self.mocks[-1].assert_not_called()

    def test_iterative_mode_is_explicit_and_bounded(self):
        self.assertEqual(0, self.call("approve-tests", "fixture", "--iterative"))
        self.control.grant.assert_called_once_with(
            "fixture", self.digest, ttl_seconds=1800, allow_edits=True, max_runs=5
        )

    def test_decline_does_not_grant(self):
        self.mocks[-2].return_value = "no"
        self.assertNotEqual(0, self.call("approve-tests", "fixture"))
        self.control.grant.assert_not_called()

    def test_install_requires_current_pass(self):
        self.control.latest_pass.return_value = None
        self.assertNotEqual(0, self.call("install", "fixture"))
        self.workspace.export.assert_not_called()
        self.mocks[-1].assert_not_called()

    def test_install_uses_existing_interactive_host_cli_without_yes(self):
        self.assertEqual(0, self.call("install", "fixture", "--enable"))
        commands = [item.args[0] for item in self.mocks[-1].call_args_list]
        self.assertEqual(2, len(commands))
        self.assertIn("personal_assistant.cli.main", commands[0])
        self.assertIn("install", commands[0])
        self.assertEqual(str(self.root / "exported"), commands[0][-1])
        self.assertNotIn("--yes", commands[0])
        self.assertEqual(["extension", "enable", "fixture.plugin"], commands[1][-3:])
        self.assertEqual(sys.executable, commands[0][0])

    def test_failed_install_never_enables(self):
        self.mocks[-1].return_value.returncode = 3
        self.assertNotEqual(0, self.call("install", "fixture", "--enable"))
        self.assertEqual(1, self.mocks[-1].call_count)

    def test_upgrade_is_explicit(self):
        self.assertEqual(0, self.call("install", "fixture", "--upgrade"))
        command = self.mocks[-1].call_args.args[0]
        self.assertEqual(
            ["extension", "upgrade", "fixture.plugin", str(self.root / "exported")],
            command[-4:],
        )

    def test_non_loopback_admin_is_rejected(self):
        self.assertNotEqual(
            0, self.call("install", "fixture", "--admin-url", "http://example.test:8001")
        )
        self.mocks[-1].assert_not_called()

    def test_rollback_requires_local_confirmation(self):
        self.mocks[-2].return_value = "no"
        self.assertNotEqual(0, self.call("rollback", "fixture.plugin"))
        self.mocks[-1].assert_not_called()
        self.mocks[-2].return_value = "YES"
        self.assertEqual(0, self.call("rollback", "fixture.plugin"))
        self.assertEqual(
            ["extension", "rollback", "fixture.plugin"],
            self.mocks[-1].call_args.args[0][-3:],
        )

    def test_real_terminal_check_rejects_piped_confirmation(self):
        function = self.real_terminal
        with (
            patch.object(sys.stdin, "isatty", return_value=False),
            self.assertRaises(ValueError),
        ):
            function()

    def test_approve_digest_mismatch_does_not_grant(self):
        self.assertNotEqual(
            0, self.call("approve-tests", "fixture", "--digest", "wrong-digest")
        )
        self.control.grant.assert_not_called()
        self.mocks[-1].assert_not_called()

    def test_install_digest_mismatch_does_not_export_or_execute(self):
        self.assertNotEqual(
            0, self.call("install", "fixture", "--digest", "wrong-digest")
        )
        self.workspace.export.assert_not_called()
        self.mocks[-1].assert_not_called()
