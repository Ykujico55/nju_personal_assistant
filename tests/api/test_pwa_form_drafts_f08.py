"""F08.3 public form-draft API contracts, using the in-memory adapter."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_assistant.app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.domain import ConcurrentModificationError
from personal_assistant.settings import Settings

SCHEMA = {
    "type": "object",
    "properties": {
        "material": {"type": "string", "title": "材料", "maxLength": 100},
        "confirmed": {"type": "boolean", "title": "确认"},
    },
    "required": ["material"],
    "additionalProperties": False,
}


class PwaFormDraftApiTests(unittest.TestCase):
    def setUp(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        self.container = build_container(settings)
        self.client = TestClient(create_app(settings=settings, container=self.container))

        async def forms() -> tuple[dict[str, object], ...]:
            return (
                {
                    "extension_id": "example.echo",
                    "extension_version": "1.0.0",
                    "id": "example.form",
                    "json_schema": SCHEMA,
                    "ui_schema": {"material": {"ui:placeholder": "请输入"}},
                },
            )

        self.container.extension_supervisor.list_forms = forms
        created = self.client.post(
            "/api/v1/tasks",
            json={"objective": "form draft"},
            headers={"Idempotency-Key": "f083-task"},
        )
        self.assertEqual(202, created.status_code, created.text)
        self.task_id = created.json()["id"]
        self.path = f"/api/v1/tasks/{self.task_id}/form-draft"

    def _create(self) -> dict[str, object]:
        response = self.client.post(
            self.path,
            json={"extension_id": "example.echo", "form_id": "example.form"},
            headers={"Idempotency-Key": "f083-open"},
        )
        self.assertEqual(201, response.status_code, response.text)
        return response.json()

    def test_create_replays_original_receipt_after_provider_is_disabled_or_upgraded(self) -> None:
        original = self._create()

        async def disabled() -> tuple[dict[str, object], ...]:
            return ()

        self.container.extension_supervisor.list_forms = disabled
        replay_disabled = self.client.post(
            self.path,
            json={"extension_id": "example.echo", "form_id": "example.form"},
            headers={"Idempotency-Key": "f083-open"},
        )
        self.assertEqual(201, replay_disabled.status_code, replay_disabled.text)
        self.assertEqual(original, replay_disabled.json())

        async def upgraded() -> tuple[dict[str, object], ...]:
            return ({
                "extension_id": "example.echo", "extension_version": "2.0.0",
                "id": "example.form", "json_schema": SCHEMA, "ui_schema": {},
            },)

        self.container.extension_supervisor.list_forms = upgraded
        replay_upgraded = self.client.post(
            self.path,
            json={"extension_id": "example.echo", "form_id": "example.form"},
            headers={"Idempotency-Key": "f083-open"},
        )
        self.assertEqual(201, replay_upgraded.status_code, replay_upgraded.text)
        self.assertEqual(original, replay_upgraded.json())
        self.assertEqual("1.0.0", self.client.get(self.path).json()["extension_version"])

        reused = self.client.post(
            self.path,
            json={"extension_id": "example.echo", "form_id": "other.form"},
            headers={"Idempotency-Key": "f083-open"},
        )
        self.assertEqual(409, reused.status_code, reused.text)

    def test_durable_version_conflict_replay_and_sources(self) -> None:
        catalog = self.client.get("/api/v1/forms")
        self.assertEqual(200, catalog.status_code, catalog.text)
        self.assertEqual("no-store", catalog.headers["cache-control"])
        self.assertEqual("example.form", catalog.json()["items"][0]["id"])

        created = self._create()
        self.assertEqual(1, created["version"])
        self.assertEqual({}, created["values"])
        self.assertEqual("UNKNOWN", created["sources"]["material"])
        self.assertEqual("no-store", self.client.get(self.path).headers["cache-control"])

        body = {"version": 1, "values": {"material": "private answer"}}
        headers = {"Idempotency-Key": "f083-edit-1"}
        saved = self.client.put(self.path, json=body, headers=headers)
        self.assertEqual(200, saved.status_code, saved.text)
        self.assertEqual(2, saved.json()["version"])
        self.assertEqual("USER_INPUT", saved.json()["sources"]["material"])
        replay = self.client.put(self.path, json=body, headers=headers)
        self.assertEqual(saved.json(), replay.json())

        stale = self.client.put(
            self.path,
            json={"version": 1, "values": {"material": "other device"}},
            headers={"Idempotency-Key": "f083-stale"},
        )
        self.assertEqual(409, stale.status_code, stale.text)
        self.assertEqual("CONCURRENT_MODIFICATION", stale.json()["error"]["code"])
        self.assertEqual(2, self.client.get(self.path).json()["version"])
        self.assertEqual("private answer", self.client.get(self.path).json()["values"]["material"])

        changed_key = self.client.put(
            self.path,
            json={"version": 1, "values": {"material": "different"}},
            headers=headers,
        )
        self.assertEqual(409, changed_key.status_code)
        self.assertEqual(2, self.client.get(self.path).json()["version"])

        # A fresh app/client reading the same store must recover the saved draft.
        app = create_app(settings=self.container.settings, container=self.container)
        with TestClient(app) as fresh:
            restored = fresh.get(self.path)
        self.assertEqual(200, restored.status_code)
        self.assertEqual("private answer", restored.json()["values"]["material"])

    def test_unsupported_schema_and_extra_values_fail_closed(self) -> None:
        async def unsupported() -> tuple[dict[str, object], ...]:
            return (
                {
                    "extension_id": "example.echo",
                    "extension_version": "1.0.0",
                    "id": "example.form",
                    "json_schema": {**SCHEMA, "$ref": "https://evil.invalid/script"},
                    "ui_schema": {},
                },
            )

        self.container.extension_supervisor.list_forms = unsupported
        refused = self.client.post(
            self.path,
            json={"extension_id": "example.echo", "form_id": "example.form"},
            headers={"Idempotency-Key": "f083-unsupported"},
        )
        self.assertEqual(422, refused.status_code, refused.text)
        self.assertEqual(404, self.client.get(self.path).status_code)

        async def supported() -> tuple[dict[str, object], ...]:
            return (
                {
                    "extension_id": "example.echo",
                    "extension_version": "1.0.0",
                    "id": "example.form",
                    "json_schema": SCHEMA,
                    "ui_schema": {},
                },
            )

        self.container.extension_supervisor.list_forms = supported
        self._create()
        invalid = self.client.put(
            self.path,
            json={"version": 1, "values": {"unlisted": "secret"}},
            headers={"Idempotency-Key": "f083-extra"},
        )
        self.assertEqual(422, invalid.status_code, invalid.text)
        self.assertEqual(1, self.client.get(self.path).json()["version"])

    def test_trusted_evidence_source_survives_unrelated_user_edit(self) -> None:
        form = {
            "extension_id": "example.echo",
            "extension_version": "1.0.0",
            "id": "example.form",
            "json_schema": SCHEMA,
            "ui_schema": {},
        }
        asyncio.run(
            self.container.form_drafts.create(
                self.task_id,
                form,
                key="seed-evidence",
                initial_values={"material": "verified evidence"},
                initial_sources={"material": "EVIDENCE"},
            )
        )
        read = self.client.get(self.path).json()
        self.assertEqual("EVIDENCE", read["sources"]["material"])
        self.assertEqual("UNKNOWN", read["sources"]["confirmed"])
        edited = self.client.put(
            self.path,
            json={"version": 1, "values": {"material": "verified evidence", "confirmed": True}},
            headers={"Idempotency-Key": "edit-confirmed"},
        )
        self.assertEqual(200, edited.status_code, edited.text)
        self.assertEqual("EVIDENCE", edited.json()["sources"]["material"])
        self.assertEqual("USER_INPUT", edited.json()["sources"]["confirmed"])

        invalid_form = {**form, "ui_schema": {"material": {"ui:widget": "javascript"}}}
        second = self.client.post(
            "/api/v1/tasks",
            json={"objective": "invalid form"},
            headers={"Idempotency-Key": "invalid-form-task"},
        ).json()["id"]
        with self.assertRaisesRegex(Exception, "unsupported widget"):
            asyncio.run(
                self.container.form_drafts.create(second, invalid_form, key="invalid-widget")
            )

    def test_trusted_seed_bool_retry_conflicts_with_integer_request_in_memory(self) -> None:
        form = {
            "extension_id": "example.echo",
            "extension_version": "1.0.0",
            "id": "example.count",
            "json_schema": {
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "additionalProperties": False,
            },
            "ui_schema": {},
        }
        original = asyncio.run(self.container.form_drafts.create(
            self.task_id, form, key="typed-seed",
            initial_values={"count": 1}, initial_sources={"count": "EVIDENCE"},
        ))
        replay = asyncio.run(self.container.form_drafts.create(
            self.task_id, form, key="typed-seed",
            initial_values={"count": 1}, initial_sources={"count": "EVIDENCE"},
        ))
        self.assertEqual(original, replay)
        with self.assertRaises(ConcurrentModificationError):
            asyncio.run(self.container.form_drafts.create(
                self.task_id, form, key="typed-seed",
                initial_values={"count": True}, initial_sources={"count": "EVIDENCE"},
            ))
        saved = asyncio.run(self.container.form_drafts.get(self.task_id))
        self.assertIs(type(saved.values["count"]), int)
