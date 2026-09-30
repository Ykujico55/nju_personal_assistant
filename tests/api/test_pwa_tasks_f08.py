from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_assistant.app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.settings import Settings


class PwaTaskReadTests(unittest.TestCase):
    def setUp(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        self.client = TestClient(
            create_app(settings=settings, container=build_container(settings))
        )

    def _create(self, objective: str) -> dict[str, object]:
        response = self.client.post(
            "/api/v1/tasks",
            json={"objective": objective},
            headers={"Idempotency-Key": f"create-{len(objective)}-{objective.replace(' ', '-')}"},
        )
        self.assertEqual(202, response.status_code, response.text)
        return response.json()

    def test_recent_tasks_are_bounded_ordered_and_page_without_duplicates(self) -> None:
        first = self._create("first")
        second = self._create("second")
        third = self._create("third")

        page = self.client.get("/api/v1/tasks?limit=2")
        self.assertEqual(200, page.status_code, page.text)
        self.assertEqual("no-store", page.headers["cache-control"])
        self.assertEqual([third["id"], second["id"]], [t["id"] for t in page.json()["items"]])
        self.assertEqual(second["id"], page.json()["next_before"])

        older = self.client.get(
            "/api/v1/tasks", params={"limit": 2, "before": page.json()["next_before"]}
        )
        self.assertEqual(200, older.status_code, older.text)
        self.assertEqual([first["id"]], [t["id"] for t in older.json()["items"]])
        self.assertIsNone(older.json()["next_before"])

        self.assertEqual(422, self.client.get("/api/v1/tasks?limit=0").status_code)
        self.assertEqual(422, self.client.get("/api/v1/tasks?limit=101").status_code)
        self.assertEqual(
            404,
            self.client.get("/api/v1/tasks?before=task_missing").status_code,
        )

    def test_task_detail_reads_current_server_state_and_is_never_cached(self) -> None:
        task = self._create("read current state")
        task_id = task["id"]
        before = self.client.get(f"/api/v1/tasks/{task_id}")
        self.assertEqual("no-store", before.headers["cache-control"])
        self.assertEqual("QUEUED", before.json()["task"]["state"])

        added = self.client.post(
            f"/api/v1/tasks/{task_id}/messages",
            json={"version": task["version"], "content": "new context"},
            headers={"Idempotency-Key": "message-current"},
        )
        self.assertEqual(200, added.status_code, added.text)
        after = self.client.get(f"/api/v1/tasks/{task_id}")
        self.assertEqual(added.json()["task"]["version"], after.json()["task"]["version"])
        self.assertEqual(["new context"], [m["content"] for m in after.json()["messages"]])

    def test_message_conflict_reports_409_and_preserves_current_server_detail(self) -> None:
        task = self._create("material gap")
        task_id = task["id"]
        first = self.client.post(
            f"/api/v1/tasks/{task_id}/messages",
            json={"version": task["version"], "content": "worker asks for material"},
            headers={"Idempotency-Key": "material-worker"},
        )
        self.assertEqual(200, first.status_code, first.text)

        stale = self.client.post(
            f"/api/v1/tasks/{task_id}/messages",
            json={"version": task["version"], "content": "my answer"},
            headers={"Idempotency-Key": "material-user"},
        )
        self.assertEqual(409, stale.status_code, stale.text)
        self.assertEqual("CONCURRENT_MODIFICATION", stale.json()["error"]["code"])
        current = self.client.get(f"/api/v1/tasks/{task_id}")
        self.assertEqual(first.json()["task"]["version"], current.json()["task"]["version"])
        self.assertEqual(
            ["worker asks for material"],
            [message["content"] for message in current.json()["messages"]],
        )

    def test_message_receipt_uses_stripped_content_and_replays_once(self) -> None:
        task = self._create("normalize material answer")
        task_id = task["id"]
        headers = {"Idempotency-Key": "material-with-edge-spaces"}
        payload = {"version": task["version"], "content": "  same material answer  "}
        first = self.client.post(f"/api/v1/tasks/{task_id}/messages", json=payload, headers=headers)
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual("same material answer", first.json()["messages"][0]["content"])

        replay = self.client.post(
            f"/api/v1/tasks/{task_id}/messages", json=payload, headers=headers
        )
        self.assertEqual(200, replay.status_code, replay.text)
        self.assertEqual(first.json()["task"]["version"], replay.json()["task"]["version"])
        self.assertEqual(1, len(replay.json()["messages"]))

    def test_sse_cursor_reads_only_later_events(self) -> None:
        task = self._create("event source")
        first = self.client.get("/api/v1/events?after=0&once=true")
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual("no-store", first.headers["cache-control"])
        self.assertIn(f'"task_id": "{task["id"]}"', first.text)
        self.assertIn("event: task.queued", first.text)
        sequence = int(first.text.split("id: ", 1)[1].split("\n", 1)[0])
        resumed = self.client.get(
            "/api/v1/events?once=true", headers={"Last-Event-ID": str(sequence)}
        )
        self.assertEqual(200, resumed.status_code)
        self.assertNotIn("event: task.queued", resumed.text)

    def test_pwa_task_views_and_static_only_worker_are_served(self) -> None:
        page = self.client.get("/ui/")
        self.assertEqual(200, page.status_code)
        self.assertIn('id="task-list"', page.text)
        self.assertIn('id="task-detail"', page.text)
        self.assertIn('id="connection-state"', page.text)

        worker = self.client.get("/ui/service-worker.js")
        self.assertEqual(200, worker.status_code)
        self.assertNotIn("/api/", worker.text)
        self.assertIn("STATIC.includes(url.pathname)", worker.text)


if __name__ == "__main__":
    unittest.main()
