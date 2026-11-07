"""Exercise the coding loop with a real test subprocess and frozen install material."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_assistant_sdk import InvocationContext, Outcome, RuntimeContext

from plugin_developer import cli
from plugin_developer.control import ControlStore
from plugin_developer.validation import candidate_digest
from plugin_developer.worker import PluginDeveloperExtension


class DevelopmentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_fail_repair_pass_and_human_install_bridge(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            worker = PluginDeveloperExtension()
            await worker.initialize(RuntimeContext(
                "1", "assistant.plugin_developer", "0.1.0", "fixture",
                non_secret_config={"workspace_root": str(workspace)},
            ))
            self.addAsyncCleanup(worker.shutdown)
            calls = 0

            async def invoke(name, arguments):
                nonlocal calls
                calls += 1
                return await worker.invoke(
                    name, arguments,
                    InvocationContext("fixture", "run", "2030-01-01T00:00:00Z", f"key-{calls}"),
                )

            created = await invoke("developer.workspace.create", {"extension_id": "fixture.loop"})
            self.assertEqual(Outcome.SUCCEEDED, created.outcome)
            candidate = created.output["candidate_id"]
            write = await invoke("developer.files.write", {
                "candidate_id": candidate, "path": "src/fixture_loop/logic.py",
                "content": "def answer():\n    return 41\n",
            })
            self.assertEqual(Outcome.SUCCEEDED, write.outcome)
            test = await invoke("developer.files.write", {
                "candidate_id": candidate, "path": "tests/test_logic.py",
                "content": (
                    "import unittest\nfrom fixture_loop.logic import answer\n"
                    "class LogicTests(unittest.TestCase):\n"
                    "    def test_answer(self):\n        self.assertEqual(answer(), 42)\n"
                ),
            })
            self.assertEqual(Outcome.SUCCEEDED, test.outcome)
            before = await invoke("developer.review", {"candidate_id": candidate})
            digest = before.output["candidate_hash"]
            # Simulated owner terminal, real grant creation; does not execute candidate.
            with (
                patch.object(cli, "_require_terminal"),
                patch("builtins.input", return_value="YES"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                granted = cli.main([
                    "--workspace-root", str(workspace), "approve-tests", candidate,
                    "--digest", digest, "--iterative",
                ])
            self.assertEqual(0, granted)
            failed = await invoke("developer.validate", {
                "candidate_id": candidate, "candidate_hash": digest,
            })
            self.assertEqual("FAILED", failed.output["status"], failed.output)
            self.assertEqual(1, failed.output["failures"], failed.output)
            self.assertTrue(any(
                failure["exception_type"] == "AssertionError"
                for failure in failed.output["failed_tests"]
            ))
            self.assertIsNone(ControlStore(worker._workspace()).latest_pass(candidate, digest))
            repaired = await invoke("developer.files.write", {
                "candidate_id": candidate, "path": "src/fixture_loop/logic.py",
                "content": "def answer():\n    return 42\n",
                "expected_sha256": write.output["sha256"],
            })
            self.assertEqual(Outcome.SUCCEEDED, repaired.outcome)
            after = await invoke("developer.review", {"candidate_id": candidate})
            new_digest = after.output["candidate_hash"]
            self.assertNotEqual(digest, new_digest)
            passed = await invoke("developer.validate", {
                "candidate_id": candidate, "candidate_hash": new_digest,
            })
            self.assertEqual(Outcome.SUCCEEDED, passed.outcome, passed.output)
            self.assertEqual("PASSED", passed.output["status"])
            self.assertGreaterEqual(passed.output["tests_run"], 2)
            release = await invoke("developer.release.request", {
                "candidate_id": candidate, "candidate_hash": new_digest,
            })
            self.assertEqual(Outcome.NEEDS_USER_ACTION, release.outcome)
            self.assertEqual("READY_FOR_LOCAL_INSTALL", release.output["status"])
            # Stop at the public host CLI boundary; no real Admin installation.
            with (
                patch.object(cli, "_require_terminal"),
                patch.object(cli.subprocess, "run") as host,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                host.return_value.returncode = 0
                result = cli.main([
                    "--workspace-root", str(workspace), "install", candidate,
                    "--digest", new_digest, "--enable",
                ])
                self.assertEqual(0, result)
                self.assertEqual(2, host.call_count)
                command = host.call_args_list[0].args[0]
                exported = Path(command[-1])
                self.assertEqual(new_digest, candidate_digest(exported))
                self.assertNotIn("--yes", command)
                self.assertEqual(
                    ["extension", "enable", "fixture.loop"],
                    host.call_args_list[1].args[0][-3:],
                )
                self.assertFalse((exported / "control").exists())
                self.assertFalse((exported / "validation-logs").exists())
