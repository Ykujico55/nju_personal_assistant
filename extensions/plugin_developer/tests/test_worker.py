"""Agent-facing coding tools; only synthetic candidates and mocked validation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_assistant_sdk import InvocationContext, Outcome, RiskLevel, RuntimeContext

from plugin_developer.control import ControlStore
from plugin_developer.worker import PluginDeveloperExtension


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = PluginDeveloperExtension()
        await self.worker.initialize(RuntimeContext(
            "1", "assistant.plugin_developer", "0.1.0", "ext_developer",
            non_secret_config={"workspace_root": str(self.root)},
        ))
        self.addAsyncCleanup(self.worker.shutdown)
        self.context = InvocationContext("task", "run", "2030-01-01T00:00:00Z", "command-1")

    async def call(self, name, args, *, key=None):
        context = self.context
        if key:
            context = InvocationContext("task", "run", "2030-01-01T00:00:00Z", key)
        return await self.worker.invoke(name, args, context)

    async def create(self):
        result = await self.call(
            "developer.workspace.create", {"extension_id": "fixture.sample", "version": "0.1.0"}
        )
        self.assertEqual(Outcome.SUCCEEDED, result.outcome)
        return result.output["candidate_id"]

    def assert_private_control_not_exposed(self, value):
        encoded = json.dumps(value, ensure_ascii=False)
        self.assertNotIn(str(self.root), encoded)
        for secret in ("run_token", "confirmation_nonce", "approved_digest", "RAW_PRIVATE_ERROR"):
            self.assertNotIn(secret, encoded)

    async def test_missing_workspace_is_user_action(self):
        worker = PluginDeveloperExtension()
        await worker.initialize(RuntimeContext("1", "assistant.plugin_developer", "0.1.0", "ns"))
        self.addAsyncCleanup(worker.shutdown)
        result = await worker.invoke("developer.candidates.list", {}, self.context)
        self.assertEqual(Outcome.NEEDS_USER_ACTION, result.outcome)
        self.assertEqual("WORKSPACE_CONFIGURATION_REQUIRED", result.output["code"])

    async def test_candidate_edit_review_and_cas_keep_control_private(self):
        candidate = await self.create()
        written = await self.call("developer.files.write", {
            "candidate_id": candidate, "path": "tests/test_public.py",
            "content": "import unittest\n\nclass PublicTest(unittest.TestCase):\n"
                       "    def test_public(self):\n        self.assertEqual(1, 1)\n",
        })
        self.assertEqual(Outcome.SUCCEEDED, written.outcome)
        loaded = await self.call("developer.files.read", {
            "candidate_id": candidate, "path": "tests/test_public.py", "limit": 20,
        })
        self.assertEqual(20, len(loaded.output["content"]))
        self.assertIsNotNone(loaded.output["next_offset"])
        self.assertEqual(
            written.output["sha256"],
            hashlib.sha256((self.worker._workspace().read(
                candidate, "tests/test_public.py"
            )).encode()).hexdigest(),
        )
        rejected = await self.call("developer.files.write", {
            "candidate_id": candidate, "path": "tests/test_public.py",
            "content": "changed", "expected_sha256": "0" * 64,
        })
        self.assertNotEqual(Outcome.SUCCEEDED, rejected.outcome)
        review = await self.call("developer.review", {"candidate_id": candidate})
        self.assertEqual(Outcome.SUCCEEDED, review.outcome)
        self.assertIn("candidate_hash", review.output)
        self.assertIn("static_review", review.output)
        self.assert_private_control_not_exposed(review.output)

    async def test_model_cannot_authorize_validation_or_install(self):
        candidate = await self.create()
        review = await self.call("developer.review", {"candidate_id": candidate})
        with patch("plugin_developer.worker.run_validation") as runner:
            result = await self.call("developer.validate", {
                "candidate_id": candidate, "candidate_hash": review.output["candidate_hash"],
                "approved": True, "approved_digest": review.output["candidate_hash"],
            })
        self.assertEqual(Outcome.NEEDS_USER_ACTION, result.outcome)
        runner.assert_not_called()
        self.assert_private_control_not_exposed(result.output)
        names = {tool.id for tool in self.worker.tools()}
        self.assertFalse(names & {"developer.install", "developer.enable", "developer.grant"})

    async def test_local_grant_runs_once_and_release_only_requests_human_install(self):
        candidate = await self.create()
        review = await self.call("developer.review", {"candidate_id": candidate})
        digest = review.output["candidate_hash"]
        store = ControlStore(self.worker._workspace())
        store.grant(candidate, digest)
        with patch("plugin_developer.worker.run_validation", return_value={
            "status": "PASSED", "candidate_hash": digest, "tests_run": 1,
            "failures": 0, "errors": 0, "skipped": 0, "exit_code": 0,
            "stdout": "RAW_PRIVATE_ERROR", "log_path": str(self.root / "private.log"),
        }) as runner:
            args = {"candidate_id": candidate, "candidate_hash": digest}
            first = await self.call("developer.validate", args)
            again = await self.call("developer.validate", args)
        self.assertEqual(Outcome.SUCCEEDED, first.outcome)
        self.assertEqual("PASSED", first.output["status"])
        self.assertEqual(first.output, again.output)
        runner.assert_called_once()
        self.assert_private_control_not_exposed(first.output)
        released = await self.call("developer.release.request", args)
        self.assertEqual(Outcome.NEEDS_USER_ACTION, released.outcome)
        self.assertTrue(released.output["install_confirmation_required"])
        self.assertNotIn("installed", released.output)
        self.assert_private_control_not_exposed(released.output)

    async def test_failed_validation_is_failed_and_cannot_promote(self):
        candidate = await self.create()
        review = await self.call("developer.review", {"candidate_id": candidate})
        digest = review.output["candidate_hash"]
        ControlStore(self.worker._workspace()).grant(candidate, digest)
        with patch("plugin_developer.worker.run_validation", return_value={
            "status": "FAILED", "candidate_hash": digest, "tests_run": 1,
            "failures": 1, "errors": 0, "skipped": 0, "exit_code": 1,
            "failed_tests": [{"test_id": "test_public", "exception_type": "AssertionError"}],
        }):
            result = await self.call("developer.validate", {
                "candidate_id": candidate, "candidate_hash": digest,
            })
        self.assertEqual(Outcome.PERMANENT, result.outcome)
        self.assertEqual("FAILED", result.output["status"])
        released = await self.call("developer.release.request", {
            "candidate_id": candidate, "candidate_hash": digest,
        })
        self.assertNotEqual("READY_FOR_LOCAL_INSTALL", released.output.get("status"))

    async def test_unknown_runner_result_is_not_reexecuted(self):
        candidate = await self.create()
        review = await self.call("developer.review", {"candidate_id": candidate})
        digest = review.output["candidate_hash"]
        ControlStore(self.worker._workspace()).grant(candidate, digest)
        with patch("plugin_developer.worker.run_validation", side_effect=OSError(
            "RAW_PRIVATE_ERROR"
        )) as runner:
            args = {"candidate_id": candidate, "candidate_hash": digest}
            first = await self.call("developer.validate", args)
            second = await self.call("developer.validate", args)
        self.assertNotEqual(Outcome.SUCCEEDED, first.outcome)
        self.assertNotEqual(Outcome.SUCCEEDED, second.outcome)
        runner.assert_called_once()
        self.assert_private_control_not_exposed(first.output)

    async def test_cancel_waits_for_validation_cleanup_and_persists_nonpass(self):
        import threading

        candidate = await self.create()
        digest = (await self.call("developer.review", {"candidate_id": candidate})).output[
            "candidate_hash"
        ]
        ControlStore(self.worker._workspace()).grant(candidate, digest)
        started = threading.Event()
        finished = threading.Event()

        def validation(*args, cancel_event=None, **kwargs):
            started.set()
            if not cancel_event.wait(5):
                raise RuntimeError("cancel signal missing")
            finished.set()
            return {
                "status": "CANCELLED", "candidate_hash": digest, "tests_run": 0, "exit_code": -1,
            }

        with patch("plugin_developer.worker.run_validation", side_effect=validation):
            task = asyncio.create_task(self.call("developer.validate", {
                "candidate_id": candidate, "candidate_hash": digest,
            }))
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(finished.is_set())
        self.assertTrue((await self.worker.drain(0)).drained)
        self.assertIsNone(ControlStore(self.worker._workspace()).latest_pass(candidate, digest))


    async def test_cancel_preserved_when_runner_cleanup_raises(self):
        import threading

        candidate = await self.create()
        digest = (await self.call("developer.review", {"candidate_id": candidate})).output[
            "candidate_hash"
        ]
        store = ControlStore(self.worker._workspace())
        store.grant(candidate, digest)
        started = threading.Event()

        def broken_cleanup(*args, cancel_event=None, **kwargs):
            started.set()
            cancel_event.wait(5)
            raise OSError("RAW_PRIVATE_ERROR")

        with patch("plugin_developer.worker.run_validation", side_effect=broken_cleanup):
            task = asyncio.create_task(self.call("developer.validate", {
                "candidate_id": candidate, "candidate_hash": digest,
            }))
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNone(store.latest_pass(candidate, digest))
        status = store.status(candidate)
        self.assertIn("UNKNOWN", json.dumps(status))
        self.assert_private_control_not_exposed(status)


class ManifestTests(unittest.TestCase):
    def test_tools_match_published_schemas_and_have_no_admin_shortcut(self):
        plugin = Path(__file__).resolve().parents[1]
        manifest = tomllib.loads((plugin / "extension.toml").read_text(encoding="utf-8"))
        tools = {tool.id: tool for tool in PluginDeveloperExtension().tools()}
        self.assertEqual(set(tools), {tool["id"] for tool in manifest["tools"]})
        self.assertEqual(9, len(tools))
        self.assertIs(RiskLevel.INTERNAL_WRITE, tools["developer.validate"].risk)
        self.assertIs(RiskLevel.READ, tools["developer.review"].risk)
        for item in manifest["tools"]:
            descriptor = tools[item["id"]]
            self.assertEqual(item["risk"], descriptor.risk.value)
            for field in ("input_schema", "output_schema"):
                self.assertEqual(
                    getattr(descriptor, field),
                    json.loads((plugin / item[field]).read_text(encoding="utf-8")),
                )
        self.assertTrue(all("install" not in name for name in tools))
