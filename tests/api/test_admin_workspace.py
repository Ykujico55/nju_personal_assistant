"""Local workspace control stays separate from per-plugin configuration."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from personal_assistant.admin_app import create_app
from personal_assistant.bootstrap import build_container
from tests.api.test_admin_extension_config import _settings


def _fixture(tmp_path):
    settings = _settings(tmp_path)
    container = build_container(settings)
    store = container.extension_config_store
    return settings, container, store


def test_workspace_routes_delegate_without_touching_plugin_configs(tmp_path):
    settings, container, store = _fixture(tmp_path)
    value = {
        "config": {"workspace_id": "instance", "roots": []},
        "config_schema": {},
        "application": {"state": "PENDING"},
    }
    digest = "sha256:" + "b" * 64
    with (
        patch.object(store, "get_workspace", AsyncMock(return_value=value), create=True) as get,
        patch.object(store, "save_workspace", AsyncMock(return_value=value), create=True) as save,
        patch.object(store, "apply_workspace", AsyncMock(return_value=value), create=True) as apply,
    ):
        with TestClient(
            create_app(settings=settings, container=container),
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 50000),
            headers={"X-Assistant-Workbench": "1"},
        ) as client:
            headers = {"Idempotency-Key": "workspace-test"}
            read = client.get("/admin/v1/workspace")
            assert read.status_code == 200
            assert read.json() == value
            assert read.headers["Cache-Control"] == "no-store"
            put = client.put(
                "/admin/v1/workspace", json={"config": value["config"]}, headers=headers
            )
            assert put.status_code == 200
            accepted = client.post(
                "/admin/v1/workspace/apply", json={"expected_digest": digest}, headers=headers
            )
            assert accepted.status_code == 200
            invalid = client.post(
                "/admin/v1/workspace/apply", json={"expected_digest": "wrong"}, headers=headers
            )
            assert invalid.status_code == 422
        get.assert_awaited_once()
        save.assert_awaited_once_with(value["config"])
        apply.assert_awaited_once_with(digest)


def test_workspace_store_conflict_preserves_error_code(tmp_path):
    from personal_assistant.core.extensions.config import ExtensionConfigError

    settings, container, store = _fixture(tmp_path)
    with (
        patch.object(store, "get_workspace", AsyncMock(), create=True),
        patch.object(store, "save_workspace", AsyncMock(), create=True),
        patch.object(
            store,
            "apply_workspace",
            AsyncMock(side_effect=ExtensionConfigError("CONFIG_REVISION_CONFLICT")),
            create=True,
        ),
    ):
        with TestClient(
            create_app(settings=settings, container=container),
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 50000),
            headers={"X-Assistant-Workbench": "1"},
        ) as client:
            response = client.post(
                "/admin/v1/workspace/apply",
                json={"expected_digest": "sha256:" + "b" * 64},
                headers={"Idempotency-Key": "workspace-conflict"},
            )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFIG_REVISION_CONFLICT"
        assert "roots" not in json.dumps(response.json())


def test_workspace_api_save_apply_conflict_and_explicit_revocation(tmp_path):
    settings, container, _store = _fixture(tmp_path)
    root = tmp_path / "notes"
    root.mkdir()
    config = {
        "workspace_id": "instance",
        "roots": [{"key": "notes", "label": "资料", "path": str(root), "mode": "read_only"}],
    }
    headers = {"Idempotency-Key": "workspace-control"}
    with TestClient(
        create_app(settings=settings, container=container),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
        headers={"X-Assistant-Workbench": "1"},
    ) as client:
        saved = client.put("/admin/v1/workspace", json={"config": config}, headers=headers)
        assert saved.status_code == 200, saved.text
        first = saved.json()["application"]
        assert first["state"] == "PENDING"
        assert first["saved_digest"] != first["desired_digest"]
        applied = client.post(
            "/admin/v1/workspace/apply",
            json={"expected_digest": first["saved_digest"]},
            headers=headers,
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["application"]["desired_digest"] == first["saved_digest"]
        assert applied.json()["application"]["state"] != "APPLIED"  # No running consumers.
        revoked = client.put(
            "/admin/v1/workspace",
            json={"config": {"workspace_id": "instance", "roots": []}},
            headers=headers,
        )
        assert revoked.status_code == 200
        second = revoked.json()["application"]
        assert second["desired_digest"] == first["saved_digest"]  # Save alone does not revoke.
        conflict = client.post(
            "/admin/v1/workspace/apply",
            json={"expected_digest": first["saved_digest"]},
            headers=headers,
        )
        assert conflict.status_code == 409
        final = client.post(
            "/admin/v1/workspace/apply",
            json={"expected_digest": second["saved_digest"]},
            headers=headers,
        )
        assert final.status_code == 200, final.text
        assert final.json()["config"]["roots"] == []
        assert final.json()["application"]["desired_digest"] == second["saved_digest"]


def test_workspace_invalid_path_and_origin_do_not_change_saved_config(tmp_path):
    settings, container, _store = _fixture(tmp_path)
    headers = {"Idempotency-Key": "workspace-invalid"}
    with TestClient(
        create_app(settings=settings, container=container),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
        headers={"X-Assistant-Workbench": "1"},
    ) as client:
        initial = client.get("/admin/v1/workspace").json()
        path = tmp_path / "private-nonexistent"
        response = client.put(
            "/admin/v1/workspace",
            json={
                "config": {
                    "workspace_id": "instance",
                    "roots": [
                        {"key": "private", "label": "资料", "path": str(path), "mode": "read_write"}
                    ],
                }
            },
            headers=headers,
        )
        assert response.status_code == 422
        assert "private-nonexistent" not in response.text
        foreign = client.put(
            "/admin/v1/workspace",
            json={"config": {"workspace_id": "instance", "roots": []}},
            headers={**headers, "Origin": "https://foreign.invalid"},
        )
        assert foreign.status_code == 403
        final = client.get("/admin/v1/workspace").json()
        assert final["config"] == initial["config"]
        assert final["application"]["saved_digest"] == initial["application"]["saved_digest"]


def test_conflicting_plugin_workspace_binding_returns_known_validation_failure(tmp_path):
    from types import SimpleNamespace

    settings, container, _store = _fixture(tmp_path)
    schema = {
        "type": "object",
        "properties": {
            "workspace_binding": {"type": "string"},
            "roots": {"type": "array", "items": {"type": "object"}},
        },
        "additionalProperties": False,
    }
    with (
        patch(
            "personal_assistant.admin_app._resolve_manifest",
            AsyncMock(return_value=SimpleNamespace(version="1.0.0")),
        ),
        patch("personal_assistant.admin_app._config_schema", return_value=schema),
        TestClient(
            create_app(settings=settings, container=container), raise_server_exceptions=False
        ) as client,
    ):
        response = client.put(
            "/admin/v1/extensions/fixture.reader/config",
            json={"config": {"workspace_binding": "instance", "roots": []}},
            headers={"Idempotency-Key": "ambiguous-binding"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_EXTENSION_CONFIG"
