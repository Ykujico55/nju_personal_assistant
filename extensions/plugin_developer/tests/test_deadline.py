from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from personal_assistant_sdk import InvocationContext, Outcome, RuntimeContext

from plugin_developer import worker as worker_module
from plugin_developer.control import ControlStore
from plugin_developer.worker import PluginDeveloperExtension


class DeadlineBudgetTests(unittest.TestCase):
    def test_budget_reserves_cleanup_time_and_caps_configuration(self):
        now = datetime(2030, 1, 1, tzinfo=UTC)
        budget = worker_module._validation_budget
        self.assertEqual(50.0, budget("2030-01-01T00:01:00Z", 120, now=now))
        self.assertEqual(30.0, budget("2030-01-01T00:01:00Z", 30, now=now))
        self.assertEqual(30.0, budget("2030-01-01T00:01:00Z", "bad", now=now))
        self.assertEqual(5.0, budget("2030-01-01T00:00:15Z", 30, now=now))

    def test_invalid_naive_non_utc_and_too_short_deadlines_are_rejected(self):
        now = datetime(2030, 1, 1, tzinfo=UTC)
        for value in (
            "bad", "2030-01-01T00:01:00", "2030-01-01T08:01:00+08:00",
            "2030-01-01T00:00:10Z", "2029-01-01T00:00:00Z",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                worker_module._validation_budget(value, 30, now=now)


class WorkerDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.worker = PluginDeveloperExtension()
        await self.worker.initialize(RuntimeContext(
            "1", "assistant.plugin_developer", "0.1.0", "ext_developer",
            non_secret_config={
                "workspace_root": self.temp.name, "validation_timeout_seconds": 120,
            },
        ))
        self.addAsyncCleanup(self.worker.shutdown)
        self.workspace = self.worker._workspace()
        self.identifier = self.workspace.create("fixture.deadline", "0.1.0")
        self.digest = self.workspace.review(self.identifier)["candidate_hash"]
        self.control = ControlStore(self.workspace)
        self.control.grant(self.identifier, self.digest)

    def result(self):
        return {
            "status": "PASSED", "candidate_hash": self.digest,
            "tests_run": 1, "failures": 0, "errors": 0, "skipped": 0, "exit_code": 0,
        }

    async def test_invalid_deadline_does_not_consume_authorization_or_start_runner(self):
        for deadline in ("2001-01-01T00:00:00Z", "not-a-date", "2030-01-01T00:00:00"):
            with self.subTest(deadline=deadline):
                with patch(
                    "plugin_developer.worker.run_validation", return_value=self.result()
                ) as runner:
                    response = await self.worker.invoke(
                        "developer.validate",
                        {"candidate_id": self.identifier, "candidate_hash": self.digest},
                        InvocationContext("task", "run", deadline, deadline),
                    )
                self.assertNotEqual(Outcome.SUCCEEDED, response.outcome)
                runner.assert_not_called()
                status = self.control.status(self.identifier)
                self.assertEqual(1, status["grant"]["remaining_runs"])
                self.assertEqual([], status["runs"])

    async def test_live_context_deadline_caps_runner_before_host_timeout(self):
        deadline = (datetime.now(UTC) + timedelta(seconds=25)).isoformat()
        with patch("plugin_developer.worker.run_validation", return_value=self.result()) as runner:
            response = await self.worker.invoke(
                "developer.validate",
                {"candidate_id": self.identifier, "candidate_hash": self.digest},
                InvocationContext("task", "run", deadline, "deadline-run"),
            )
        self.assertEqual(Outcome.SUCCEEDED, response.outcome)
        timeout = runner.call_args.kwargs["timeout_seconds"]
        self.assertGreaterEqual(timeout, 1.0)
        self.assertLessEqual(timeout, 15.0)
