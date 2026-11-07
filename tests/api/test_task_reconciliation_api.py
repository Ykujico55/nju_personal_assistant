"""Explicit task reconciliation has no caller-supplied evidence or implicit dispatch."""
import os
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from personal_assistant.admin_app import create_app as admin_app
from personal_assistant.app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.settings import Settings
from tests.api.test_cloudflare_access import ORIGIN, RealVerifierFactory, trusted_settings
from tests.unit import test_task_reconciliation as runtime_cases


@pytest.mark.parametrize("local", [False, True])
def test_explicit_reconciliation_route_reuses_receipt_and_refuses_forged_results(local):
    with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
        root = Path(directory)
        settings = replace(Settings.from_env(), extension_root=root / "extensions",
                           artifact_root=root / "artifacts")
        container = build_container(settings)
        app = (admin_app if local else create_app)(settings=settings, container=container)
        prefix = "/admin/v1/workbench" if local else "/api/v1"
        runtime = SimpleNamespace(reconcile=AsyncMock(return_value={
            "task_id": "task_fixture", "resolved": True, "state": "WAITING_USER",
        }))
        with TestClient(app, base_url="http://127.0.0.1:8001",
                        client=("127.0.0.1", 50000)) as client:
            if local:
                client.headers["X-Assistant-Workbench"] = "1"
            route = prefix + "/tasks/task_fixture/reconcile"
            headers = {"Idempotency-Key": "reconcile-original"}
            with patch("personal_assistant.api.v1.delivery.build_runtime", return_value=runtime):
                first = client.post(route, json={"version": 3}, headers=headers)
                assert first.status_code == 200, first.text
                assert first.headers["cache-control"] == "no-store"
                replay = client.post(route, json={"version": 3}, headers=headers)
                assert replay.json() == first.json()
                assert runtime.reconcile.await_count == 1
                runtime.reconcile.assert_awaited_once_with(
                    "task_fixture", version=3, key="reconcile:reconcile-original")
                changed = client.post(route, json={"version": 4}, headers=headers)
                assert changed.status_code == 409
                for invalid in ({"version": True}, {"version": "3"},
                                {"version": 3, "resolved": True},
                                {"version": 3, "receipt": {"state": "SUCCEEDED"}}):
                    denied = client.post(route, json=invalid,
                                         headers={"Idempotency-Key": "forged"})
                    assert denied.status_code == 422, denied.text
                assert runtime.reconcile.await_count == 1
                missing_key = client.post(route, json={"version": 3})
                assert missing_key.status_code == 400
                if local:
                    denied = client.post(route, json={"version": 3}, headers={
                        **headers, "Origin": "https://untrusted.invalid"})
                    assert denied.status_code == 403


class AuthenticatedRuntimeReconciliationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = runtime_cases.TaskReconciliationTests.asyncSetUp
    generate_unknown_checkpoint = runtime_cases.TaskReconciliationTests.generate_unknown_checkpoint
    ready = runtime_cases.TaskReconciliationTests.ready

    async def test_authenticated_api_resolves_real_unknown_checkpoint_without_redispatch(self):
        runtime, task, executor = await self.ready()
        original_proof = runtime.reconciliation.resolve.side_effect
        runtime.reconciliation.resolve.side_effect = None
        runtime.reconciliation.resolve.return_value = None
        verifier = RealVerifierFactory()
        self.addAsyncCleanup(verifier.client.aclose)
        settings = replace(
            trusted_settings(), extension_root=self.container.settings.extension_root,
            artifact_root=self.container.settings.artifact_root,
        )
        app = create_app(settings=settings, container=self.container,
                         access_verifier=verifier.verifier)
        route = f"/api/v1/tasks/{task.id}/reconcile"
        body = {"version": task.version}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url=ORIGIN) as client:
            with patch("personal_assistant.api.v1.delivery.build_runtime", return_value=runtime):
                rejected = await client.post(route, json=body,
                    headers={"Idempotency-Key": "without-auth", "Origin": ORIGIN})
                self.assertEqual(401, rejected.status_code)
                runtime.reconciliation.resolve.assert_not_awaited()
                headers = {"Idempotency-Key": "first-check", "Origin": ORIGIN,
                           "X-Requested-With": "personal-assistant-pwa",
                           "Sec-Fetch-Site": "same-origin",
                           "Cf-Access-Jwt-Assertion": verifier.token()}
                missing = await client.post(route, json=body, headers=headers)
                self.assertEqual(200, missing.status_code, missing.text)
                self.assertFalse(missing.json()["resolved"])
                self.assertEqual(self.before_snapshot,
                                 await runtime.checkpoints.load("run_" + task.id))
                ordinary = await client.post(f"/api/v1/tasks/{task.id}/execution",
                    json={"version": task.version, "mode": "agent"},
                    headers={**headers, "Idempotency-Key": "ordinary-continue"})
                self.assertEqual(422, ordinary.status_code, ordinary.text)
                runtime.reconciliation.resolve.side_effect = original_proof
                replay_missing = await client.post(route, json=body, headers=headers)
                self.assertEqual(missing.json(), replay_missing.json())
                runtime.reconciliation.resolve.assert_awaited_once()
                headers["Idempotency-Key"] = "new-check-after-host-proof"
                resolved = await client.post(route, json=body, headers=headers)
                self.assertEqual(200, resolved.status_code, resolved.text)
                self.assertTrue(resolved.json()["resolved"])
                self.assertEqual("WAITING_USER", resolved.json()["state"])
                self.assertEqual("no-store", resolved.headers["cache-control"])
                replay = await client.post(route, json=body, headers=headers)
                self.assertEqual(resolved.json(), replay.json())
                self.assertEqual(2, runtime.reconciliation.resolve.await_count)
                snapshot = await runtime.checkpoints.load("run_" + task.id)
                self.assertEqual("matched-1", snapshot["observations"][-1]["receipt_id"])
                self.assertEqual(1, len(await runtime.records.list("reconciliations")))
                self.assertEqual(self.approval_before,
                                 await runtime.approvals.get(self.approval_before.id))
                self.assertFalse(await runtime.work_once())
                self.assertEqual(1, executor.calls)
                resumed = await client.post(f"/api/v1/tasks/{task.id}/execution",
                    json={"version": resolved.json()["version"], "mode": "agent"},
                    headers={**headers, "Idempotency-Key": "explicit-continue"})
                self.assertEqual(202, resumed.status_code, resumed.text)
                await runtime.work_once()
                self.assertEqual("SUCCEEDED", (await runtime.tasks.get(task.id)).state.value)
                self.assertEqual(1, executor.calls)
