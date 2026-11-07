from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from personal_assistant.admin_app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.core.models import ModelOutput
from personal_assistant.settings import Settings

PREFIX = "/admin/v1/workbench"


class WorkbenchApiTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        with patch.dict(os.environ, {}, clear=True):
            self.settings = replace(
                Settings.from_env(),
                extension_root=root / "extensions",
                artifact_root=root / "artifacts",
                model_remote_base_url="https://fixture.example.test",
                model_remote_model="fixture",
                model_remote_secret_handle="fixture",
            )
        self.container = build_container(self.settings)
        self.client = TestClient(
            create_app(settings=self.settings, container=self.container),
            base_url="http://127.0.0.1:8001",
            client=("127.0.0.1", 50000),
        )
        self.client.headers["X-Assistant-Workbench"] = "1"

    def post(self, path, body, key):
        return self.client.post(PREFIX + path, json=body, headers={"Idempotency-Key": key})

    def test_local_only_header_host_origin_and_remote_peer(self):
        self.assertEqual(200, self.client.get(PREFIX + "/system").status_code)
        self.assertEqual(
            403,
            self.client.get(PREFIX + "/system", headers={"X-Assistant-Workbench": ""}).status_code,
        )
        self.assertEqual(
            403,
            self.client.get(
                PREFIX + "/system", headers={"Origin": "https://evil.example"}
            ).status_code,
        )
        self.assertEqual(
            403, self.client.get(PREFIX + "/system", headers={"Host": "evil.example"}).status_code
        )
        remote = TestClient(
            create_app(settings=self.settings, container=self.container),
            base_url="http://127.0.0.1:8001",
            client=("192.0.2.1", 50000),
        )
        self.assertEqual(
            403, remote.get(PREFIX + "/system", headers={"X-Assistant-Workbench": "1"}).status_code
        )

    def test_reused_task_api_version_and_pending_boundary(self):
        created = self.post("/tasks", {"objective": "local fixture"}, "create")
        self.assertEqual(202, created.status_code, created.text)
        task = created.json()
        self.assertEqual("no-store", created.headers["cache-control"])
        replay = self.post("/tasks", {"objective": "local fixture"}, "create")
        self.assertEqual(task, replay.json())
        stale = self.post(
            f"/tasks/{task['id']}/messages", {"content": "hello", "version": 0}, "stale"
        )
        self.assertEqual(409, stale.status_code)
        message = self.post(
            f"/tasks/{task['id']}/messages",
            {"content": "hello", "version": task["version"]},
            "message",
        )
        self.assertEqual(200, message.status_code, message.text)
        self.assertEqual("local-admin", message.json()["messages"][0]["actor"])
        self.assertEqual(1, len(self.client.get(PREFIX + "/tasks").json()["items"]))

    def test_chat_preview_has_no_call_and_confirmation_is_bound_idempotent(self):
        assert self.container.model_router is not None
        model = AsyncMock(
            return_value=ModelOutput(
                text='{"answer":"4"}',
                provider_id="remote.openai",
                model_id="fixture",
                usage={"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            )
        )
        self.container.model_router.complete = model
        body = {"message": "2+2?", "history": [{"role": "user", "content": "only maths"}]}
        preview = self.post("/chat/preview", body, "preview")
        self.assertEqual(200, preview.status_code, preview.text)
        self.assertEqual(0, model.await_count)
        bad = self.post(
            "/chat/confirm",
            {
                **body,
                "message": "changed",
                "preview_hash": preview.json()["preview_hash"],
            },
            "bad",
        )
        self.assertEqual(409, bad.status_code, bad.text)
        confirmed = {**body, "preview_hash": preview.json()["preview_hash"]}
        first = self.post("/chat/confirm", confirmed, "confirm")
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual("4", first.json()["answer"])
        self.assertEqual(
            {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}, first.json()["usage"]
        )
        self.assertEqual(first.json(), self.post("/chat/confirm", confirmed, "confirm").json())
        self.assertEqual(1, model.await_count)
        request = model.call_args.args[0]
        self.assertEqual("workbench.chat", request.purpose)
        self.assertIn("No tools", request.instruction)
        self.assertEqual("SENSITIVE", request.fields[0].classification.value)
        self.assertEqual([], self.client.get(PREFIX + "/tasks").json()["items"])
        self.container.model_router = None
        self.assertEqual(first.json(), self.post("/chat/confirm", confirmed, "confirm").json())

    def test_chat_rejects_tool_proposals_and_invalid_history(self):
        assert self.container.model_router is not None
        self.container.model_router.complete = AsyncMock(
            return_value=ModelOutput(
                text='{"kind":"CALL_TOOL","tool_id":"anything"}',
                provider_id="remote.openai",
                model_id="fixture",
            )
        )
        body = {"message": "hello", "history": []}
        preview = self.post("/chat/preview", body, "p").json()
        response = self.post(
            "/chat/confirm",
            {
                **body,
                "preview_hash": preview["preview_hash"],
            },
            "c",
        )
        self.assertEqual(502, response.status_code)
        self.assertEqual(
            422,
            self.post(
                "/chat/preview",
                {"message": "hello", "history": [{"role": "system", "content": "x"}]},
                "wrong-role",
            ).status_code,
        )

    def test_api_enqueues_and_existing_worker_runs_same_task_without_second_agent(self):
        from personal_assistant.runtime import build_runtime

        assert self.container.model_router is not None
        model = AsyncMock(
            return_value=ModelOutput(
                text='{"kind":"COMPLETE","message":"fixture result","result":{"answer":4}}',
                provider_id="remote.openai",
                model_id="fixture",
            )
        )
        self.container.model_router.complete = model
        with self.client as client:
            task = self.post("/tasks", {"objective": "isolated queue fixture"}, "queued").json()
            self.assertEqual("QUEUED", task["state"])
            self.assertEqual(0, model.await_count)
            runtime = build_runtime(self.container)
            self.assertTrue(client.portal.call(runtime.work_once))
            current = client.get(PREFIX + "/tasks/" + task["id"]).json()
            self.assertEqual("SUCCEEDED", current["task"]["state"])
            self.assertEqual("fixture result", current["messages"][-1]["content"])
            self.assertEqual(1, model.await_count)
            self.assertFalse(client.portal.call(runtime.work_once))
            self.assertEqual("agent.plan", model.call_args.args[0].purpose)

    def test_usage_guard_and_workbench_absent_from_public_app(self):
        from personal_assistant.app import create_app as public_app

        response = self.client.get(PREFIX + "/usage")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertEqual(
            403,
            self.client.get(PREFIX + "/usage", headers={"X-Assistant-Workbench": ""}).status_code,
        )
        public = TestClient(public_app(settings=self.settings, container=self.container))
        self.assertEqual(
            404, public.get(PREFIX + "/usage", headers={"X-Assistant-Workbench": "1"}).status_code
        )

    def test_bad_model_result_is_terminal_and_same_key_never_calls_again(self):
        assert self.container.model_router is not None
        model = AsyncMock(
            return_value=ModelOutput(
                text="not json",
                provider_id="remote.openai",
                model_id="fixture",
                usage={"total_tokens": 7},
            )
        )
        self.container.model_router.complete = model
        body = {"message": "fixture", "history": []}
        preview = self.post("/chat/preview", body, "bad-preview").json()
        command = {**body, "preview_hash": preview["preview_hash"]}
        first = self.post("/chat/confirm", command, "bad-repeat")
        retry = self.post("/chat/confirm", command, "bad-repeat")
        self.assertEqual(502, first.status_code)
        self.assertEqual(first.json()["error"]["code"], retry.json()["error"]["code"])
        self.assertEqual(1, model.await_count)

    def test_model_transport_failure_stays_unknown_and_retry_cannot_dispatch_again(self):
        assert self.container.model_router is not None
        model = AsyncMock(side_effect=TimeoutError("fixture timeout"))
        self.container.model_router.complete = model
        body = {"message": "fixture", "history": []}
        preview = self.post("/chat/preview", body, "timeout-preview").json()
        command = {**body, "preview_hash": preview["preview_hash"]}
        first = self.post("/chat/confirm", command, "timeout-repeat")
        retry = self.post("/chat/confirm", command, "timeout-repeat")
        self.assertEqual(409, first.status_code)
        self.assertEqual("CHAT_RESULT_UNKNOWN", first.json()["error"]["code"])
        self.assertEqual(first.json()["error"]["code"], retry.json()["error"]["code"])
        self.assertEqual(1, model.await_count)

    def test_concurrent_same_key_and_cancelled_claim_never_redispatch(self):
        import asyncio
        from types import SimpleNamespace

        from personal_assistant.api.errors import ApplicationError
        from personal_assistant.api.workbench import ChatConfirmation, chat_confirm

        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()

            async def complete(*args, **kwargs):
                entered.set()
                await release.wait()
                return ModelOutput(
                    text='{"answer":"done"}', provider_id="remote.openai", model_id="fixture"
                )

            model = AsyncMock(side_effect=complete)
            self.container.model_router.complete = model
            request = SimpleNamespace(state=SimpleNamespace(idempotency_key="concurrent"))
            payload = ChatConfirmation(**confirmed)
            first = asyncio.create_task(
                chat_confirm(payload, request, self.container, "local-admin")
            )
            await entered.wait()
            try:
                with self.assertRaises(ApplicationError) as error:
                    await chat_confirm(payload, request, self.container, "local-admin")
                self.assertEqual("CHAT_RESULT_UNKNOWN", error.exception.code)
                self.assertEqual(1, model.await_count)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                with self.assertRaises(ApplicationError) as cancelled:
                    await chat_confirm(payload, request, self.container, "local-admin")
                self.assertEqual("CHAT_RESULT_UNKNOWN", cancelled.exception.code)
                self.assertEqual(1, model.await_count)
            finally:
                release.set()
                if not first.done():
                    first.cancel()
                    await asyncio.gather(first, return_exceptions=True)

        body = {"message": "fixture", "history": []}
        preview = self.post("/chat/preview", body, "concurrent-preview").json()
        confirmed = {**body, "preview_hash": preview["preview_hash"]}
        with self.client as client:
            client.portal.call(scenario)

    def test_trace_only_projects_tool_identity_not_arguments(self):
        from personal_assistant.domain import TaskRun

        async def seed(task_id):
            run = TaskRun(id="run_" + task_id, task_id=task_id, objective="fixture")
            await self.container.run_repository.add(run)
            await self.container.checkpoint_store.save(
                run,
                {
                    "phase": "dispatching",
                    "risk": "EXTERNAL_WRITE",
                    "pending_call": {
                        "tool_id": "fixture.tool",
                        "tool_version": "1",
                        "arguments": {"private_fixture": "never-return"},
                        "target": {"secret": "never-return"},
                    },
                },
            )

        with self.client as client:
            task = self.post("/tasks", {"objective": "trace fixture"}, "trace").json()
            client.portal.call(seed, task["id"])
            response = client.get(PREFIX + "/tasks/" + task["id"] + "/trace")
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual(
                {
                    "phase": "dispatching",
                    "pending_tool": {
                        "tool_id": "fixture.tool",
                        "extension_id": None,
                        "risk": "EXTERNAL_WRITE",
                    },
                },
                {key: response.json()[key] for key in ("phase", "pending_tool")},
            )
            self.assertTrue(response.json()["requires_reconciliation"])
            self.assertFalse(response.json()["toolset"]["can_recreate"])
            self.assertEqual("UNBOUND", response.json()["toolset"]["state"])
            self.assertNotIn("never-return", response.text)
            self.assertNotIn("arguments", response.text)

    def test_trace_projects_only_allowlisted_validation_diagnostic(self):
        from personal_assistant.domain import TaskRun

        async def seed(task_id, code):
            run = TaskRun(id="run_" + task_id, task_id=task_id, objective="fixture")
            await self.container.checkpoint_store.save(run, {
                "phase": "waiting",
                "validation_failure": {"code": code, "message": "private-value-not-for-output"},
            })

        with self.client as client:
            task = self.post("/tasks", {"objective": "validation fixture"}, "validation").json()
            client.portal.call(seed, task["id"], "PROPOSAL_INVALID_ARGUMENTS")
            response = client.get(PREFIX + "/tasks/" + task["id"] + "/trace")
            self.assertEqual(200, response.status_code)
            self.assertEqual(
                "PROPOSAL_INVALID_ARGUMENTS", response.json()["validation_failure"]["code"]
            )
            self.assertNotIn("private-value-not-for-output", response.text)
            client.portal.call(seed, task["id"], "private-unrecognized-code")
            response = client.get(PREFIX + "/tasks/" + task["id"] + "/trace")
            self.assertNotIn("validation_failure", response.json())
            self.assertNotIn("private-", response.text)

    def _run_registered_read_tool(self, *, frozen):
        import json
        from types import SimpleNamespace

        from personal_assistant.core.tools import ToolGateway, ToolPolicy
        from personal_assistant.domain import RiskLevel, ToolDescriptor
        from personal_assistant.runtime import build_runtime

        descriptor = ToolDescriptor(
            id="fixture.calculator",
            version="1",
            extension_id="fixture",
            extension_version="1",
            risk=RiskLevel.READ,
            input_schema={
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"sum": {"type": "integer"}},
                "required": ["sum"],
                "additionalProperties": False,
            },
        )
        executor = SimpleNamespace(execute=AsyncMock(return_value={"sum": 4}))
        model = AsyncMock(
            side_effect=[
                ModelOutput(
                    text=json.dumps(
                        {
                            "kind": "CALL_TOOL",
                            "tool_id": descriptor.id,
                            "tool_version": "1",
                            "arguments": {"a": 2, "b": 2},
                        }
                    ),
                    provider_id="remote.openai",
                    model_id="fixture",
                ),
                ModelOutput(
                    text=json.dumps(
                        {"kind": "COMPLETE", "message": "计算工具返回4", "result": {"sum": 4}}
                    ),
                    provider_id="remote.openai",
                    model_id="fixture",
                ),
            ]
        )
        self.container.model_router.complete = model
        with self.client as client:
            # Lifecycle registry refresh has finished; no extension is imported or installed.
            self.container.tool_registry.publish((descriptor,))
            gateway = ToolGateway(
                registry=self.container.tool_registry,
                policy=ToolPolicy(),
                approvals=self.container.approvals,
                executor=executor,
                outbox=self.container.side_effect_outbox,
            )
            gateway.invoke = AsyncMock(wraps=gateway.invoke)
            self.container.tool_gateway = gateway
            if frozen:
                self.container.host_control.freeze()
            created = self.post(
                "/tasks", {"objective": "使用已启用计算工具计算2+2"}, "catalog-tool"
            )
            self.assertEqual(202, created.status_code, created.text)
            task = created.json()
            self.assertEqual(0, model.await_count)
            self.assertEqual(0, executor.execute.await_count)
            runtime = build_runtime(self.container)
            self.assertTrue(client.portal.call(runtime.work_once))
            detail = client.get(PREFIX + "/tasks/" + task["id"]).json()
            if frozen:
                self.assertEqual("PAUSED_SAFETY", detail["task"]["state"])
                self.assertEqual(0, model.await_count)
                self.assertEqual(0, gateway.invoke.await_count)
                self.assertEqual(0, executor.execute.await_count)
                self.assertTrue(self.container.host_control.frozen())
            else:
                self.assertEqual("SUCCEEDED", detail["task"]["state"])
                self.assertEqual(2, model.await_count)
                self.assertIn(descriptor.id, model.call_args_list[0].args[0].instruction)
                self.assertEqual(1, gateway.invoke.await_count)
                self.assertEqual(1, executor.execute.await_count)
                executed_descriptor, arguments, context = executor.execute.call_args.args
                self.assertEqual(descriptor.id, executed_descriptor.id)
                self.assertEqual({"a": 2, "b": 2}, dict(arguments))
                self.assertEqual(task["id"], context.task_id)
                observations = next(
                    field.value
                    for field in model.call_args_list[1].args[0].fields
                    if field.name == "observations"
                )
                self.assertEqual([{"sum": 4}], json.loads(observations))
                self.assertEqual("计算工具返回4", detail["messages"][-1]["content"])
            self.assertFalse(client.portal.call(runtime.work_once))

    def test_api_worker_model_selects_registered_plugin_through_real_gateway_once(self):
        self._run_registered_read_tool(frozen=False)

    def test_frozen_api_task_does_not_dispatch_model_or_plugin(self):
        self._run_registered_read_tool(frozen=True)
