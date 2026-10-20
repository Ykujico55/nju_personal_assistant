from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from personal_assistant.admin_app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.settings import Settings


class AdminOperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.dict(os.environ, {}, clear=True):
            settings = replace(
                Settings.from_env(),
                extension_root=Path(self.tmp.name) / "extensions",
                artifact_root=Path(self.tmp.name) / "artifacts",
            )
        self.container = build_container(settings)
        self.client = TestClient(create_app(settings=settings, container=self.container))

    def test_local_freeze_is_explicit_and_not_available_in_public_api(self):
        self.assertEqual(200, self.client.get("/admin/v1/host").status_code)
        response = self.client.post(
            "/admin/v1/host/freeze", json={}, headers={"Idempotency-Key": "freeze"}
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(response.json()["frozen"])
        from personal_assistant.app import create_app as public_app

        public = TestClient(public_app(settings=self.container.settings, container=self.container))
        self.assertEqual(
            404,
            public.post(
                "/api/v1/host/freeze", json={}, headers={"Idempotency-Key": "f"}
            ).status_code,
        )

    def test_schedule_only_accepts_published_source_and_same_key_replays(self):
        async def catalog(method):
            if method == "schedule.list":
                return (
                    {
                        "id": "fixture.hourly",
                        "extension_id": "fixture",
                        "extension_version": "1",
                        "interval_seconds": 3600,
                        "cron": None,
                        "misfire_policy": "coalesce",
                        "timezone": "Asia/Shanghai",
                    },
                )
            if method == "event_source.list":
                return (
                    {
                        "id": "fixture.events",
                        "extension_id": "fixture",
                        "extension_version": "1",
                        "event_types": ["fixture.changed"],
                    },
                )
            return ()

        self.container.extension_supervisor.list_declarations = AsyncMock(side_effect=catalog)
        body = {
            "schedule_id": "fixture.hourly",
            "source_id": "fixture.events",
            "extension_id": "fixture",
            "extension_version": "1",
        }
        route = "/admin/v1/schedules"
        first = self.client.post(route, json=body, headers={"Idempotency-Key": "schedule"})
        self.assertEqual(201, first.status_code, first.text)
        self.container.extension_supervisor.list_declarations = AsyncMock(return_value=())
        replay = self.client.post(route, json=body, headers={"Idempotency-Key": "schedule"})
        self.assertEqual(first.json(), replay.json())
        bad = self.client.post(
            route, json={**body, "source_id": "other.events"}, headers={"Idempotency-Key": "bad"}
        )
        self.assertEqual(422, bad.status_code, bad.text)
