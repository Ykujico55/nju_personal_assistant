from __future__ import annotations

import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_assistant.admin_app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.domain import TaskRun, TaskState
from personal_assistant.settings import Settings

PREFIX = "/admin/v1/workbench"


class WorkbenchQueueTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        with patch.dict(os.environ, {}, clear=True):
            settings = replace(
                Settings.from_env(),
                extension_root=root / "extensions",
                artifact_root=root / "artifacts",
            )
        # Explicit queue timestamps avoid a Windows clock tie falling through to random job IDs.
        ticks = count()
        epoch = datetime.now(UTC) - timedelta(days=1)
        self.enterContext(patch(
            "personal_assistant.infrastructure.memory.job_queue._utcnow",
            side_effect=lambda: epoch + timedelta(milliseconds=next(ticks)),
        ))
        self.container = build_container(settings)
        self.client = self.enterContext(
            TestClient(
                create_app(settings=settings, container=self.container),
                base_url="http://127.0.0.1:8001",
                client=("127.0.0.1", 50000),
            )
        )
        self.client.headers["X-Assistant-Workbench"] = "1"

    def post(self, path, body, key):
        return self.client.post(PREFIX + path, json=body, headers={"Idempotency-Key": key})

    def create(self, key):
        response = self.post("/tasks", {"objective": key}, key)
        self.assertEqual(202, response.status_code, response.text)
        return response.json()

    def listing(self):
        response = self.client.get(PREFIX + "/task-queue")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("no-store", response.headers["cache-control"])
        return response.json()["items"]

    def test_reorder_changes_actual_worker_claim_and_replay_is_not_a_second_move(self):
        first, second, third = [self.create(x) for x in ("first", "second", "third")]
        items = self.listing()
        self.assertEqual([first["id"], second["id"], third["id"]], [x["id"] for x in items])
        row = items[2]
        body = {"job_id": row["queue_job_id"], "version": row["queue_version"], "direction": "up"}
        route = f"/task-queue/{third['id']}/move"
        response = self.post(route, body, "move")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(response.json(), self.post(route, body, "move").json())
        self.assertEqual(
            [first["id"], third["id"], second["id"]], [x["id"] for x in self.listing()]
        )

        async def claim():
            return await self.container.jobs.claim(worker_id="fixture")

        job = self.client.portal.call(claim)
        self.assertEqual(first["id"], job.payload["task_id"])
        claimed = self.post(
            f"/task-queue/{first['id']}/move",
            {"job_id": job.id, "version": job.version, "direction": "bottom"},
            "claimed",
        )
        self.assertEqual(409, claimed.status_code, claimed.text)

    def test_remove_cancels_ready_job_preserves_task_and_receipt_and_other_work(self):
        first, second = [self.create(x) for x in ("remove-me", "keep-me")]
        original = self.listing()[0]
        body = {"version": first["version"]}
        route = f"/task-queue/{first['id']}/remove"
        removed = self.post(route, body, "remove")
        self.assertEqual(200, removed.status_code, removed.text)
        self.assertEqual(removed.json(), self.post(route, body, "remove").json())
        self.assertEqual([second["id"]], [x["id"] for x in self.listing()])
        history = self.client.get(PREFIX + f"/tasks/{first['id']}").json()
        self.assertEqual("CANCELLED", history["task"]["state"])
        job = self.client.portal.call(self.container.jobs.get, original["queue_job_id"])
        self.assertEqual("FAILED", job.state.value)
        self.assertEqual("TASK_CANCELLED", job.error_code)

        async def claim():
            return await self.container.jobs.claim(worker_id="fixture")

        self.assertEqual(second["id"], self.client.portal.call(claim).payload["task_id"])
        self.assertEqual(
            2, len(self.client.get(PREFIX + "/task-queue?include_removed=true").json()["items"])
        )

    def test_stale_remove_and_foreign_job_are_rejected_without_mutation(self):
        first, second = [self.create(x) for x in ("a", "b")]
        old = self.post(f"/task-queue/{first['id']}/remove", {"version": 0}, "stale")
        self.assertEqual(409, old.status_code, old.text)
        other = self.listing()[1]
        response = self.post(
            f"/task-queue/{first['id']}/move",
            {
                "job_id": other["queue_job_id"],
                "version": other["queue_version"],
                "direction": "top",
            },
            "foreign",
        )
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual([first["id"], second["id"]], [x["id"] for x in self.listing()])

    def test_unknown_cannot_be_hidden_and_terminal_can_be_removed(self):
        task = self.create("unresolved")

        async def seed():
            await self.container.checkpoint_store.save(
                TaskRun(id="run_" + task["id"], task_id=task["id"], objective="unresolved"),
                {"unresolved_external": True},
            )

        self.client.portal.call(seed)
        response = self.post(
            f"/task-queue/{task['id']}/remove", {"version": task["version"]}, "unknown"
        )
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual(1, len(self.listing()))
        done = self.create("done")
        self.client.portal.call(self.container.tasks.project, done["id"], TaskState.SUCCEEDED)
        current = self.client.get(PREFIX + f"/tasks/{done['id']}").json()["task"]
        response = self.post(
            f"/task-queue/{done['id']}/remove", {"version": current["version"]}, "done-remove"
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(
            "SUCCEEDED", self.client.get(PREFIX + f"/tasks/{done['id']}").json()["task"]["state"]
        )

    def test_queue_is_loopback_only(self):
        self.assertEqual(
            403,
            self.client.get(
                PREFIX + "/task-queue", headers={"Origin": "https://evil.example"}
            ).status_code,
        )

    def test_late_unknown_after_cancellation_returns_to_visible_list(self):
        task = self.create("late-unknown")
        response = self.post(
            f"/task-queue/{task['id']}/remove", {"version": task["version"]}, "remove-late"
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual([], self.listing())

        async def seed():
            await self.container.checkpoint_store.save(
                TaskRun(id="run_" + task["id"], task_id=task["id"], objective="late-unknown"),
                {"unresolved_external": True},
            )

        self.client.portal.call(seed)
        row = self.listing()[0]
        self.assertEqual(task["id"], row["id"])
        self.assertTrue(row["requires_reconciliation"])
        self.assertFalse(row["removed"])
