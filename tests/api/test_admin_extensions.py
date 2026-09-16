"""F02: local Admin API management surface and public API boundary."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_assistant.admin_app import create_app as create_admin_app
from personal_assistant.app import create_app as create_public_app
from personal_assistant.bootstrap import build_container
from personal_assistant.core.extensions import (
    ExtensionRecord,
    ExtensionRegistry,
    ExtensionState,
    ManifestParser,
    compute_artifact_hash,
)
from personal_assistant.core.extensions.lifecycle import (
    InstallCoordinator,
    InstalledArtifact,
    LifecycleManager,
    StagedArtifact,
)
from personal_assistant.core.extensions.supervision import ExtensionSupervisorService
from personal_assistant.infrastructure.extensions.versions import CompatibleVersionOperator
from personal_assistant.infrastructure.memory.extensions import InMemoryLifecycleStore
from personal_assistant.infrastructure.memory.operations import (
    InMemoryExtensionOperationStore,
)
from personal_assistant.infrastructure.memory.versions import InMemoryVersionCatalog
from personal_assistant.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"


def _safe_rmtree(path: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise AssertionError(f"refusing to delete {resolved}")
    shutil.rmtree(resolved, ignore_errors=True)


class _Stager:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def stage(self, source: str) -> StagedArtifact:
        target = self._root / f"staged-{len(list(self._root.iterdir()))}"
        shutil.copytree(Path(source), target)
        return StagedArtifact(str(source), target, compute_artifact_hash(target))

    async def discard(self, staged: StagedArtifact) -> None:
        _safe_rmtree(staged.root)


class _Installer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.installs: list[str] = []

    async def install(self, staged: StagedArtifact, manifest) -> InstalledArtifact:
        del staged
        target = self.root / manifest.id / manifest.version
        (target / "payload").mkdir(parents=True, exist_ok=True)
        (target / "venv").mkdir(parents=True, exist_ok=True)
        self.installs.append(manifest.version)
        return InstalledArtifact(target, f"runtime-{manifest.version}")

    async def uninstall_code(self, record: ExtensionRecord) -> None:
        if record.install_path and Path(record.install_path).exists():
            shutil.rmtree(Path(record.install_path))

    async def remove_version(self, record: ExtensionRecord) -> None:
        await self.uninstall_code(record)

    async def clean_failed_install(self, staged: StagedArtifact) -> None:
        del staged


class _Verifier:
    async def verify(self, installed, manifest) -> None:
        del installed, manifest


class _Runtime:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []

    async def start(self, record: ExtensionRecord) -> None:
        self.started.append(record.manifest.version)

    async def health(self, record: ExtensionRecord) -> bool:
        del record
        return True

    async def drain(self, record: ExtensionRecord, deadline_epoch: float) -> None:
        del record, deadline_epoch

    async def stop(self, record: ExtensionRecord) -> None:
        self.stopped.append(record.manifest.version)

    async def stop_all(self) -> None:
        return None

    async def invoke_tool(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {"outcome": "SUCCEEDED", "output": {}}


class _DataStore:
    async def namespaces(self, extension_id: str):
        del extension_id
        return ()

    async def purge(self, extension_id: str) -> None:
        from personal_assistant.core.extensions.errors import ExtensionError

        raise ExtensionError(f"purge is not implemented for {extension_id}")


def _development_settings(tmp: Path) -> Settings:
    with patch.dict(os.environ, {}, clear=True):
        base = Settings.from_env()
    return replace(base, extension_root=tmp / "extensions", artifact_root=tmp / "artifacts")


class AdminExtensionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f02_admin_"))
        (self.tmp / "staging").mkdir()
        settings = _development_settings(self.tmp)
        container = build_container(settings)
        self.registry = ExtensionRegistry()
        self.store = InMemoryLifecycleStore()
        self.operations = InMemoryExtensionOperationStore()
        self.runtime = _Runtime()
        self.catalog = InMemoryVersionCatalog()
        self.installer = _Installer(self.tmp / "installed")
        # The API creates confirmations with the server clock, so this container
        # must use the real clock as well; a frozen clock would reject them.
        coordinator = InstallCoordinator(
            _Stager(self.tmp / "staging"),
            self.installer,
            _Verifier(),
            self.store,
        )
        manager = LifecycleManager(
            self.store,
            self.registry,
            self.runtime,
            self.installer,
            _DataStore(),
            CompatibleVersionOperator(self.catalog),
        )
        container.extension_supervisor = ExtensionSupervisorService(
            coordinator=coordinator,
            manager=manager,
            registry=self.registry,
            store=self.store,
            operations=self.operations,
            runtime=self.runtime,
        )
        container.extension_registry = self.registry
        self.settings = settings
        self.container = container

    def tearDown(self) -> None:
        _safe_rmtree(self.tmp)

    def _client(self) -> TestClient:
        return TestClient(
            create_admin_app(settings=self.settings, container=self.container),
            headers={"Idempotency-Key": "admin-test-key"},
        )

    @staticmethod
    def _wait(client: TestClient, operation_id: str, *, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = client.get(f"/admin/v1/extension-operations/{operation_id}")
            assert response.status_code == 200, response.text
            body = response.json()
            if body["status"] in {"SUCCEEDED", "FAILED"}:
                return body
            time.sleep(0.05)
        raise AssertionError(f"operation {operation_id} did not finish")

    def test_inspect_is_data_only_and_install_requires_confirmation(self) -> None:
        with self._client() as client:
            preview = client.post(
                "/admin/v1/extensions/inspect", json={"source": str(EXAMPLE)}
            )
            self.assertEqual(200, preview.status_code, preview.text)
            body = preview.json()
            self.assertEqual("example.echo", body["extension_id"])
            self.assertFalse(body["executed_code"])
            self.assertNotIn("warning", body.get("details", {}))
            self.assertEqual([], self.runtime.started)
            self.assertFalse((self.tmp / "installed").exists())

            wrong = client.post(
                "/admin/v1/extensions/install",
                json={
                    "plan_id": body["plan_id"],
                    "confirmation_nonce": "not-the-nonce",
                    "preview_hash": body["preview_hash"],
                    "accepted_warning": True,
                },
            )
            self.assertEqual(409, wrong.status_code, wrong.text)
            self.assertEqual("CONFIRMATION_REQUIRED", wrong.json()["error"]["code"])
            self.assertFalse((self.tmp / "installed").exists())

            accepted = client.post(
                "/admin/v1/extensions/install",
                json={
                    "plan_id": body["plan_id"],
                    "confirmation_nonce": body["confirmation_nonce"],
                    "preview_hash": body["preview_hash"],
                    "accepted_warning": True,
                },
            )
            self.assertEqual(202, accepted.status_code, accepted.text)
            operation = self._wait(client, accepted.json()["id"])
            self.assertEqual("SUCCEEDED", operation["status"])
            stored = asyncio.run(self.store.get("example.echo"))
            assert stored is not None
            self.assertEqual(ExtensionState.INSTALLED_DISABLED, stored.state)

    def test_full_management_flow_via_operations(self) -> None:
        with self._client() as client:
            preview = client.post(
                "/admin/v1/extensions/inspect", json={"source": str(EXAMPLE)}
            ).json()
            accepted = client.post(
                "/admin/v1/extensions/install",
                json={
                    "plan_id": preview["plan_id"],
                    "confirmation_nonce": preview["confirmation_nonce"],
                    "preview_hash": preview["preview_hash"],
                    "accepted_warning": True,
                },
            )
            self._wait(client, accepted.json()["id"])

            for operation, expected in (
                ("enable", "ENABLED"),
                ("disable", "DISABLED"),
                ("uninstall", "UNINSTALLED"),
            ):
                # Both an empty body and no body are accepted for these operations.
                response = client.post(f"/admin/v1/extensions/example.echo/{operation}", json={})
                self.assertEqual(202, response.status_code, response.text)
                result = self._wait(client, response.json()["id"])
                self.assertEqual("SUCCEEDED", result["status"], result)
                listing = client.get("/admin/v1/extensions").json()["items"]
                item = next(entry for entry in listing if entry["id"] == "example.echo")
                self.assertEqual(expected, item["state"])
            listing = client.get("/admin/v1/extensions").json()["items"]
            item = next(entry for entry in listing if entry["id"] == "example.echo")
            self.assertTrue(item["tombstone"])
            self.assertTrue(item["data_retained"])

    def test_install_replay_with_the_same_idempotency_key_returns_one_operation(self) -> None:
        with self._client() as client:
            preview = client.post(
                "/admin/v1/extensions/inspect", json={"source": str(EXAMPLE)}
            ).json()
            body = {
                "plan_id": preview["plan_id"],
                "confirmation_nonce": preview["confirmation_nonce"],
                "preview_hash": preview["preview_hash"],
                "accepted_warning": True,
            }
            first = client.post(
                "/admin/v1/extensions/install",
                json=body,
                headers={"Idempotency-Key": "replay-key"},
            )
            self.assertEqual(202, first.status_code, first.text)
            replay = client.post(
                "/admin/v1/extensions/install",
                json=body,
                headers={"Idempotency-Key": "replay-key"},
            )
            self.assertEqual(first.json()["id"], replay.json()["id"])
            final = self._wait(client, first.json()["id"])
            self.assertEqual("SUCCEEDED", final["status"], final)
            self.assertEqual(["0.1.0"], self.installer.installs)

    def test_reject_plan_allows_a_new_preview_and_keeps_active_state(self) -> None:
        with self._client() as client:
            first = client.post(
                "/admin/v1/extensions/inspect", json={"source": str(EXAMPLE)}
            ).json()
            rejected = client.post(f"/admin/v1/extension-plans/{first['plan_id']}/reject")
            self.assertEqual(200, rejected.status_code, rejected.text)
            second = client.post(
                "/admin/v1/extensions/inspect", json={"source": str(EXAMPLE)}
            )
            self.assertEqual(200, second.status_code, second.text)
            self.assertNotEqual(first["plan_id"], second.json()["plan_id"])

    def test_purge_is_explicitly_not_implemented(self) -> None:
        with self._client() as client:
            response = client.post("/admin/v1/extensions/example.echo/purge-data")
            self.assertEqual(501, response.status_code)
            self.assertEqual(
                "EXTENSION_DATA_PURGE_NOT_IMPLEMENTED", response.json()["error"]["code"]
            )

    def test_public_api_has_no_extension_management_routes(self) -> None:
        settings = _development_settings(self.tmp)
        client = TestClient(
            create_public_app(settings=settings, container=self.container),
            headers={"Idempotency-Key": "public-test-key"},
        )
        for method, path in (
            ("post", "/api/v1/extensions/install"),
            ("post", "/api/v1/extensions/example.echo/enable"),
            ("post", "/api/v1/extensions/example.echo/upgrade"),
            ("delete", "/api/v1/extensions/example.echo"),
            ("get", "/admin/v1/extensions"),
        ):
            response = getattr(client, method)(path)
            self.assertIn(response.status_code, {404, 405}, path)

    def test_admin_status_lists_persisted_records_after_install(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        asyncio.run(
            self.store.save(
                ExtensionRecord(
                    manifest=manifest,
                    artifact_hash=compute_artifact_hash(EXAMPLE),
                    state=ExtensionState.DISABLED,
                    install_path=str(self.tmp / "installed" / "example.echo" / "0.1.0"),
                    tombstone=False,
                )
            )
        )
        with self._client() as client:
            item = client.get("/admin/v1/extensions/example.echo").json()
            self.assertEqual("DISABLED", item["state"])
            self.assertEqual("0.1.0", item["version"])


if __name__ == "__main__":
    unittest.main()
