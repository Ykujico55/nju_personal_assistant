from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_assistant.app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.settings import Settings


class DeliveryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.settings = Settings.from_env()
        self.container = build_container(self.settings)
        self.client = TestClient(create_app(settings=self.settings, container=self.container))

    def test_system_capabilities_are_truthful_and_do_not_expose_secrets(self) -> None:
        response = self.client.get("/api/v1/system")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertFalse(response.json()["model_configured"])
        self.assertFalse(response.json()["worker_online"])
        for field in ("database_url", "secret_handle", "access_aud", "token"):
            self.assertNotIn(field, response.text)

    def test_execution_command_is_versioned_and_same_key_binds_same_request(self) -> None:
        task = self.client.post(
            "/api/v1/tasks", json={"objective": "fixture"}, headers={"Idempotency-Key": "create"}
        ).json()
        route = f"/api/v1/tasks/{task['id']}/execution"
        request = {"version": task["version"], "mode": "agent", "inputs": {}}
        stale = self.client.post(
            route, json={**request, "version": 0}, headers={"Idempotency-Key": "stale"}
        )
        self.assertEqual(409, stale.status_code, stale.text)
        first = self.client.post(route, json=request, headers={"Idempotency-Key": "run"})
        self.assertEqual(202, first.status_code, first.text)
        replay = self.client.post(route, json=request, headers={"Idempotency-Key": "run"})
        self.assertEqual(first.json(), replay.json())
        changed = self.client.post(
            route,
            json={**request, "inputs": {"answer": "different"}},
            headers={"Idempotency-Key": "run"},
        )
        self.assertEqual(409, changed.status_code, changed.text)
        self.assertEqual("no-store", first.headers["cache-control"])

    def test_rules_require_explicit_tests_and_cannot_grant_side_effects(self) -> None:
        bad = self.client.post(
            "/api/v1/rules",
            json={"scope": {"workflow_id": "fixture"}, "preferences": {"auto_approve": True}},
            headers={"Idempotency-Key": "bad"},
        )
        self.assertEqual(422, bad.status_code, bad.text)
        draft = self.client.post(
            "/api/v1/rules",
            json={"scope": {"workflow_id": "fixture"}, "preferences": {"language": "zh"}},
            headers={"Idempotency-Key": "draft"},
        )
        self.assertEqual(201, draft.status_code, draft.text)
        rule = draft.json()
        denied = self.client.post(
            f"/api/v1/rules/{rule['id']}/activate",
            json={"version": 1, "tests": []},
            headers={"Idempotency-Key": "activate-bad"},
        )
        self.assertEqual(422, denied.status_code, denied.text)
        self.assertEqual(
            "CANDIDATE", self.client.get("/api/v1/rules").json()["items"][0]["value"]["state"]
        )


class TaskDisclosureApiTests(unittest.TestCase):
    def test_task_disclosure_is_built_by_host_and_binds_task_version(self):
        with patch.dict(os.environ, {}, clear=True):
            base = Settings.from_env()
        settings = replace(
            base,
            model_remote_base_url="https://model.example.test",
            model_remote_model="fixture",
            model_remote_secret_handle="fixture",
        )
        container = build_container(settings)
        with TestClient(create_app(settings=settings, container=container)) as client:
            task = client.post(
                "/api/v1/tasks", json={"objective": "fixture"}, headers={"Idempotency-Key": "c"}
            ).json()
            route = f"/api/v1/tasks/{task['id']}/model-disclosure/preview"
            self.assertEqual(
                404, client.post(route, json={}, headers={"Idempotency-Key": "preview"}).status_code
            )
            from personal_assistant.domain import TaskRun

            async def seed():
                run = TaskRun(id="run_" + task["id"], task_id=task["id"], objective="fixture")
                await container.run_repository.add(run)
                await container.checkpoint_store.save(
                    run,
                    {
                        "model_disclosure": {
                            "provider_id": "remote.openai",
                            "purpose": "agent.plan",
                            "instruction": "JSON",
                            "fields": [
                                {
                                    "name": "objective",
                                    "value": "synthetic personal fixture",
                                    "source": "user",
                                    "classification": "PERSONAL",
                                }
                            ],
                        }
                    },
                )

            client.portal.call(seed)
            preview = client.post(route, json={}, headers={"Idempotency-Key": "preview2"})
            self.assertEqual(200, preview.status_code, preview.text)
            self.assertNotIn("synthetic personal fixture", preview.text)
            confirm = f"/api/v1/tasks/{task['id']}/model-disclosure"
            request = {"version": task["version"], "preview_hash": preview.json()["preview_hash"]}
            response = client.post(confirm, json=request, headers={"Idempotency-Key": "permit"})
            self.assertEqual(202, response.status_code, response.text)
            self.assertEqual(
                response.json(),
                client.post(confirm, json=request, headers={"Idempotency-Key": "permit"}).json(),
            )
            changed = client.post(
                confirm,
                json={**request, "version": task["version"] + 1},
                headers={"Idempotency-Key": "permit"},
            )
            self.assertEqual(409, changed.status_code, changed.text)


class EmbeddedAgentApiTests(unittest.TestCase):
    def test_explicit_dev_worker_processes_queue_and_stops_on_shutdown(self):
        import time

        with patch.dict(os.environ, {}, clear=True):
            settings = replace(Settings.from_env(), embedded_agent=True)
        container = build_container(settings)
        with TestClient(create_app(settings=settings, container=container)) as client:
            task = client.post(
                "/api/v1/tasks",
                json={"objective": "fixture no model"},
                headers={"Idempotency-Key": "c"},
            ).json()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                current = client.get(f"/api/v1/tasks/{task['id']}").json()["task"]
                if current["state"] != "QUEUED":
                    break
                time.sleep(0.05)
            self.assertEqual("WAITING_USER", current["state"])
            self.assertTrue(client.get("/api/v1/system").json()["worker_online"])
