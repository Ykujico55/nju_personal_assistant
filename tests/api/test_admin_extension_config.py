"""F05: generic loopback-only extension configuration endpoints."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_assistant.admin_app import create_app as create_admin_app
from personal_assistant.bootstrap import build_container
from personal_assistant.settings import Settings


def _settings(tmp: Path) -> Settings:
    with patch.dict(os.environ, {}, clear=True):
        base = Settings.from_env()
    return replace(base, extension_root=tmp / "extensions")


class AdminExtensionConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f05_admin_config_"))
        root = self.tmp / "extensions" / "demo_config"
        (root / "schemas").mkdir(parents=True)
        (root / "extension.toml").write_text(
            "\n".join(
                [
                    'manifest_version = "1"',
                    'id = "demo.config"',
                    'name = "Demo Config"',
                    'version = "0.1.0"',
                    'core_api = ">=1,<2"',
                    'python = ">=3.12,<3.14"',
                    'entrypoint = "demo_config.worker:create_extension"',
                    'dependency_lock = "requirements.lock"',
                    'config_schema = "schemas/config.json"',
                    "state_schema_version = 1",
                    'healthcheck = "system.health"',
                    'context_providers = ["demo.config_context"]',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (root / "requirements.lock").write_text("# none\n", encoding="utf-8")
        (root / "schemas" / "config.json").write_text(
            json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "roots": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}},
                                "required": ["path"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["roots"],
                    "additionalProperties": False,
                }
            ),
            encoding="utf-8",
        )
        self.settings = _settings(self.tmp)
        self.container = build_container(self.settings)
        self.container.bundled_extensions_root = self.tmp / "extensions"

    def tearDown(self) -> None:
        resolved = self.tmp.resolve()
        if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise AssertionError(f"refusing to delete {resolved}")
        shutil.rmtree(resolved, ignore_errors=True)

    def _client(self) -> TestClient:
        return TestClient(
            create_admin_app(settings=self.settings, container=self.container),
            headers={"Idempotency-Key": "config-test-key"},
        )

    def test_missing_config_returns_empty_object_and_schema(self) -> None:
        with self._client() as client:
            response = client.get("/admin/v1/extensions/demo.config/config")
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual({}, body["config"])
        self.assertIn("roots", body["config_schema"]["properties"])

    def test_put_persists_validated_config(self) -> None:
        config = {"roots": [{"path": "C:/notes"}]}
        with self._client() as client:
            response = client.put(
                "/admin/v1/extensions/demo.config/config", json={"config": config}
            )
            self.assertEqual(200, response.status_code, response.text)
            self.assertTrue(response.json()["restart_required"])
            fetched = client.get("/admin/v1/extensions/demo.config/config").json()
        self.assertEqual(config, fetched["config"])

    def test_put_rejects_schema_violations(self) -> None:
        with self._client() as client:
            response = client.put(
                "/admin/v1/extensions/demo.config/config", json={"config": {}}
            )
        self.assertEqual(422, response.status_code)
        self.assertEqual("INVALID_EXTENSION_CONFIG", response.json()["error"]["code"])

    def test_oversized_schema_fails_closed_on_read(self) -> None:
        schema = self.tmp / "extensions" / "demo_config" / "schemas" / "config.json"
        schema.write_text(
            json.dumps({"type": "object", "description": "x" * 300_000}),
            encoding="utf-8",
        )
        with self._client() as client:
            response = client.get("/admin/v1/extensions/demo.config/config")
        self.assertEqual(500, response.status_code)
        self.assertEqual(
            "EXTENSION_CONFIG_SCHEMA_UNREADABLE", response.json()["error"]["code"]
        )

    def test_unknown_extension_is_404(self) -> None:
        with self._client() as client:
            response = client.get("/admin/v1/extensions/demo.unknown/config")
        self.assertEqual(404, response.status_code)

    def test_public_api_has_no_config_route(self) -> None:
        from personal_assistant.app import create_app as create_public_app

        with TestClient(create_public_app(settings=self.settings)) as client:
            response = client.get("/admin/v1/extensions/demo.config/config")
        self.assertIn(response.status_code, {401, 403, 404, 503})


if __name__ == "__main__":
    unittest.main()
