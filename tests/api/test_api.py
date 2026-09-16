from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_assistant.app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.settings import Settings


def development_settings() -> Settings:
    with patch.dict(os.environ, {}, clear=True):
        return Settings.from_env()


class PublicApiTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = development_settings()
        self.client = TestClient(
            create_app(settings=settings, container=build_container(settings))
        )

    def test_commands_require_idempotency_key(self) -> None:
        response = self.client.post("/api/v1/tasks", json={"objective": "demo"})
        self.assertEqual(400, response.status_code)
        self.assertEqual("IDEMPOTENCY_KEY_REQUIRED", response.json()["error"]["code"])

    def test_task_create_message_cancel_and_replay(self) -> None:
        headers = {"Idempotency-Key": "create-one"}
        first = self.client.post(
            "/api/v1/tasks", json={"objective": "prepare a draft"}, headers=headers
        )
        self.assertEqual(202, first.status_code, first.text)
        replay = self.client.post(
            "/api/v1/tasks", json={"objective": "prepare a draft"}, headers=headers
        )
        self.assertEqual(first.json()["id"], replay.json()["id"])
        task_id = first.json()["id"]

        detail = self.client.get(f"/api/v1/tasks/{task_id}")
        self.assertEqual("QUEUED", detail.json()["task"]["state"])
        version = detail.json()["task"]["version"]

        message_headers = {"Idempotency-Key": "message-one"}
        message = self.client.post(
            f"/api/v1/tasks/{task_id}/messages",
            json={"version": version, "content": "extra context"},
            headers=message_headers,
        )
        self.assertEqual(200, message.status_code, message.text)
        self.assertEqual(1, len(message.json()["messages"]))
        message_replay = self.client.post(
            f"/api/v1/tasks/{task_id}/messages",
            json={"version": version, "content": "extra context"},
            headers=message_headers,
        )
        self.assertEqual(1, len(message_replay.json()["messages"]))

        cancel = self.client.post(
            f"/api/v1/tasks/{task_id}/cancel",
            json={"version": message.json()["task"]["version"]},
            headers={"Idempotency-Key": "cancel-one"},
        )
        self.assertEqual("CANCELLED", cancel.json()["state"])

    def test_idempotency_key_reuse_with_changed_payload_conflicts(self) -> None:
        headers = {"Idempotency-Key": "same-key"}
        self.assertEqual(
            202,
            self.client.post(
                "/api/v1/tasks", json={"objective": "first"}, headers=headers
            ).status_code,
        )
        changed = self.client.post(
            "/api/v1/tasks", json={"objective": "second"}, headers=headers
        )
        self.assertEqual(409, changed.status_code)
        self.assertEqual("CONCURRENT_MODIFICATION", changed.json()["error"]["code"])

    def test_example_extension_is_visible_but_not_core_special_cased(self) -> None:
        response = self.client.get("/api/v1/extensions")
        self.assertEqual(200, response.status_code, response.text)
        item = response.json()["items"][0]
        self.assertEqual("example.echo", item["id"])
        self.assertEqual("DISCOVERED", item["state"])
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])

    def test_extension_state_comes_from_the_durable_store(self) -> None:
        from personal_assistant.core.extensions import (
            ExtensionRecord,
            ExtensionState,
            ManifestParser,
            compute_artifact_hash,
        )

        example = Path(__file__).resolve().parents[2] / "extensions" / "example_echo"
        manifest = ManifestParser().parse(example)
        container = self.client.app.state.container
        record = ExtensionRecord(
            manifest=manifest,
            artifact_hash=compute_artifact_hash(example),
            state=ExtensionState.ENABLED,
            install_path=str(container.settings.extension_root / "installed" / "example.echo"),
        )
        asyncio.run(container.lifecycle_store.save(record))
        response = self.client.get("/api/v1/extensions")
        self.assertEqual(200, response.status_code)
        item = response.json()["items"][0]
        self.assertEqual("example.echo", item["id"])
        self.assertEqual("ENABLED", item["state"])

    def test_mobile_pwa_shell_is_served(self) -> None:
        response = self.client.get("/ui/")
        self.assertEqual(200, response.status_code)
        self.assertIn("Personal Assistant", response.text)
        worker = self.client.get("/ui/service-worker.js")
        self.assertEqual(200, worker.status_code)
        self.assertNotIn('"/api/', worker.text)


if __name__ == "__main__":
    unittest.main()
