"""F05: generic loopback-only extension configuration endpoints."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

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

    def test_saved_configuration_has_explicit_pending_status(self) -> None:
        with self._client() as client:
            saved = client.put(
                "/admin/v1/extensions/demo.config/config",
                json={"config": {"roots": [{"path": "C:/notes"}]}},
            ).json()
            self.assertEqual("PENDING", saved["application"]["state"])
            digest = saved["application"]["saved_digest"]
            self.assertEqual(71, len(digest))
            current = client.get("/admin/v1/extensions/demo.config/config").json()
            self.assertEqual(digest, current["application"]["saved_digest"])
            self.assertNotEqual(digest, current["application"]["desired_digest"])

    def test_apply_config_requires_enabled_installed_extension(self) -> None:
        with self._client() as client:
            response = client.post(
                "/admin/v1/extensions/demo.config/config/apply",
                json={"expected_digest": "sha256:" + "0" * 64, "extension_version": "0.1.0"},
            )
            self.assertEqual(409, response.status_code)
            self.assertEqual("EXTENSION_NOT_ENABLED", response.json()["error"]["code"])

    def test_apply_is_bound_to_saved_digest_and_installed_version(self) -> None:
        from personal_assistant.core.extensions.manifest import ManifestParser
        from personal_assistant.core.extensions.models import ExtensionRecord, ExtensionState

        manifest = ManifestParser().parse(self.tmp / "extensions" / "demo_config")
        record = ExtensionRecord(manifest, "sha256:" + "0" * 64, ExtensionState.ENABLED)
        path = "/admin/v1/extensions/demo.config/config"
        with self._client() as client:
            saved = client.put(path, json={"config": {"roots": [{"path": "C:/one"}]}}).json()
            digest = saved["application"]["saved_digest"]
            body = {"expected_digest": digest, "extension_version": "0.1.0"}
            with patch.object(
                self.container.extension_supervisor, "record", AsyncMock(return_value=record)
            ):
                applied = client.post(path + "/apply", json=body)
                self.assertEqual(200, applied.status_code, applied.text)
                first = applied.json()["application"]
                self.assertEqual(digest, first["desired_digest"])
                again = client.post(path + "/apply", json=body).json()["application"]
                self.assertEqual(first["generation"], again["generation"])
                changed = {**body, "extension_version": "0.2.0"}
                self.assertEqual(409, client.post(path + "/apply", json=changed).status_code)
                client.put(path, json={"config": {"roots": [{"path": "C:/two"}]}})
                self.assertEqual(409, client.post(path + "/apply", json=body).status_code)
                latest = client.get(path).json()
                schema_path = self.tmp / "extensions" / "demo_config" / "schemas" / "config.json"
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                schema["properties"]["roots"]["items"]["properties"]["path"]["type"] = "integer"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
                invalid = client.post(
                    path + "/apply",
                    json={
                        "expected_digest": latest["application"]["saved_digest"],
                        "extension_version": "0.1.0",
                    },
                )
                self.assertEqual(422, invalid.status_code)
                self.assertEqual("INVALID_EXTENSION_CONFIG", invalid.json()["error"]["code"])
                self.assertEqual(digest, latest["application"]["desired_digest"])
                self.assertEqual("C:/two", latest["config"]["roots"][0]["path"])

    def test_put_rejects_schema_violations(self) -> None:
        with self._client() as client:
            response = client.put("/admin/v1/extensions/demo.config/config", json={"config": {}})
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
        self.assertEqual("EXTENSION_CONFIG_SCHEMA_UNREADABLE", response.json()["error"]["code"])

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
