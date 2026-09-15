from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_assistant.admin_app import create_app as create_admin_app
from personal_assistant.app import create_app as create_public_app
from personal_assistant.bootstrap import build_container
from personal_assistant.health_app import create_app as create_health_app
from personal_assistant.settings import Settings


def development_settings() -> Settings:
    with patch.dict(os.environ, {}, clear=True):
        return Settings.from_env()


class NetworkBoundaryTests(unittest.TestCase):
    def test_cloudflare_mode_fails_closed_until_jwt_verifier_exists(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PA_TRUST_CLOUDFLARE_ACCESS": "true",
                "PA_CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
                "PA_CF_ACCESS_AUD": "audience",
                "PA_PUBLIC_ORIGIN": "https://assistant.example.test",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        client = TestClient(
            create_public_app(settings=settings, container=build_container(settings))
        )
        response = client.get("/healthz")
        self.assertEqual(503, response.status_code)
        self.assertEqual(
            "CLOUDFLARE_ACCESS_VERIFIER_NOT_IMPLEMENTED",
            response.json()["error"]["code"],
        )

    def test_health_sidecar_has_no_task_admin_or_docs_routes(self) -> None:
        client = TestClient(create_health_app())
        self.assertEqual({"status": "ok"}, client.get("/healthz").json())
        for path in ("/api/v1/tasks/anything", "/admin/v1/extensions", "/docs", "/openapi.json"):
            self.assertEqual(404, client.get(path).status_code, path)

    def test_admin_lists_but_does_not_fake_supervisor_completion(self) -> None:
        settings = development_settings()
        client = TestClient(
            create_admin_app(settings=settings, container=build_container(settings))
        )
        listed = client.get("/admin/v1/extensions")
        self.assertEqual("example.echo", listed.json()["items"][0]["id"])
        install = client.post(
            "/admin/v1/extensions/install",
            json={"source": "extensions/example_echo"},
            headers={"Idempotency-Key": "install-one"},
        )
        self.assertEqual(501, install.status_code)
        self.assertEqual(
            "EXTENSION_SUPERVISOR_NOT_IMPLEMENTED", install.json()["error"]["code"]
        )


if __name__ == "__main__":
    unittest.main()
