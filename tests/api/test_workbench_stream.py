from __future__ import annotations

import asyncio
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from personal_assistant.admin_app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.core.models import ModelOutput
from personal_assistant.runtime import build_runtime
from personal_assistant.settings import ConfigurationError, Settings

PREFIX = "/admin/v1/workbench"


def events(response):
    result = []
    for frame in response.text.split("\n\n"):
        if not frame.strip():
            continue
        lines = frame.splitlines()
        result.append((lines[0].removeprefix("event: "), json.loads(lines[1][6:])))
    return result


class WorkbenchStreamTests(unittest.TestCase):
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
                model_disclosure_mode="automatic",
            )
        self.container = build_container(self.settings)
        self.client = TestClient(
            create_app(settings=self.settings, container=self.container),
            base_url="http://127.0.0.1:8001",
            client=("127.0.0.1", 50000),
        )
        self.client.headers["X-Assistant-Workbench"] = "1"
        self.calls = []

    def post(self, body=None, key="one"):
        return self.client.post(
            PREFIX + "/chat/stream",
            json=body or {"message": "2+2?"},
            headers={"Idempotency-Key": key},
        )

    def provider(self, *, failure=False, empty=False):
        async def stream(request, **kwargs):
            self.calls.append((request, kwargs))
            yield SimpleNamespace(delta="4" if not empty else "", output=None)
            if failure:
                raise RuntimeError("private provider failure must not escape")
            yield SimpleNamespace(
                delta="",
                output=ModelOutput(
                    text="4" if not empty else "",
                    provider_id="remote.openai",
                    model_id="fixture",
                    usage={"total_tokens": 7},
                ),
            )

        self.container.model_router.stream = stream

    def test_automatic_send_streams_and_replays_completed_once(self):
        self.provider()
        response = self.post()
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        received = events(response)
        self.assertEqual(["delta", "completed"], [item[0] for item in received])
        self.assertEqual("4", received[-1][1]["answer"])
        self.assertEqual(7, received[-1][1]["usage"]["total_tokens"])
        self.assertFalse(received[-1][1]["tools_executed"])
        self.assertIsNotNone(self.calls[0][1]["consent_id"])
        self.assertEqual("text", self.calls[0][0].metadata["response_format"])
        again = events(self.post())
        self.assertEqual(["completed"], [item[0] for item in again])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(409, self.post({"message": "changed"}).status_code)

    def test_partial_failure_is_unknown_and_never_redispatches(self):
        self.provider(failure=True)
        first = events(self.post())
        self.assertEqual(["delta", "error"], [item[0] for item in first])
        self.assertTrue(first[-1][1]["uncertain"])
        self.assertNotIn("private provider failure", str(first))
        again = events(self.post())
        self.assertEqual("CHAT_RESULT_UNKNOWN", again[-1][1]["code"])
        self.assertEqual(1, len(self.calls))

    def test_invalid_output_is_terminal_and_not_retried(self):
        self.provider(empty=True)
        first = events(self.post())
        self.assertEqual("CHAT_RESPONSE_INVALID", first[-1][1]["code"])
        self.assertFalse(first[-1][1]["uncertain"])
        self.post()
        self.assertEqual(1, len(self.calls))

    def test_manual_requires_matching_preview_and_local_boundary(self):
        self.provider()
        self.container.settings = replace(self.settings, model_disclosure_mode="manual")
        self.assertEqual(409, self.post().status_code)
        self.assertFalse(self.calls)
        preview = self.client.post(
            PREFIX + "/chat/preview",
            json={"message": "2+2?"},
            headers={"Idempotency-Key": "preview"},
        ).json()
        response = self.post({"message": "2+2?", "preview_hash": preview["preview_hash"]})
        self.assertEqual("completed", events(response)[-1][0])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(
            403,
            self.client.post(
                PREFIX + "/chat/stream",
                json={"message": "x"},
                headers={"Idempotency-Key": "cross", "Origin": "https://evil.test"},
            ).status_code,
        )

    def test_policy_is_visible_and_agent_uses_real_exact_consent(self):
        self.assertEqual(
            "automatic", self.client.get(PREFIX + "/system").json()["model_disclosure_mode"]
        )
        model = AsyncMock(
            return_value=ModelOutput(
                text='{"kind":"COMPLETE","message":"done"}',
                provider_id="remote.openai",
                model_id="fixture",
            )
        )
        self.container.model_router.complete = model
        with self.client as client:
            runtime = build_runtime(self.container)
            result = client.portal.call(
                lambda: runtime.planner.propose(
                    objective="public check", messages=[], tools=(), observations=()
                )
            )
            self.assertEqual("COMPLETE", result["kind"])
            consent_id = model.call_args.kwargs["consent_id"]
            consent = client.portal.call(self.container.disclosures.get, consent_id)
            self.assertEqual("agent.plan", consent.purpose)
            self.assertEqual("remote.openai", consent.provider_id)

    def test_policy_rejects_unrecognized_value(self):
        with self.assertRaises(ConfigurationError):
            replace(self.settings, model_disclosure_mode="everyone")

    def test_live_delta_precedes_completion_and_cancelled_retry_never_calls_again(self):
        from starlette.requests import Request

        from personal_assistant.api.workbench import ChatStreamRequest, chat_stream

        async def run():
            closed = asyncio.Event()
            gate = asyncio.Event()

            async def streaming(request, **kwargs):
                self.calls.append((request, kwargs))
                try:
                    yield SimpleNamespace(delta="first", output=None)
                    await gate.wait()
                    yield SimpleNamespace(
                        delta="",
                        output=ModelOutput(
                            text="first", provider_id="remote.openai", model_id="fixture"
                        ),
                    )
                finally:
                    closed.set()

            self.container.model_router.stream = streaming
            request = Request({"type": "http", "state": {"idempotency_key": "cancel"}})
            response = await chat_stream(
                ChatStreamRequest(message="x"), request, self.container, "local-admin"
            )
            first = await anext(response.body_iterator)
            self.assertIn("event: delta", first)
            self.assertFalse(gate.is_set())
            retry = await chat_stream(
                ChatStreamRequest(message="x"), request, self.container, "local-admin"
            )
            self.assertIn("CHAT_RESULT_UNKNOWN", await anext(retry.body_iterator))
            await response.body_iterator.aclose()
            self.assertTrue(closed.is_set())
            retry = await chat_stream(
                ChatStreamRequest(message="x"), request, self.container, "local-admin"
            )
            self.assertIn("CHAT_RESULT_UNKNOWN", await anext(retry.body_iterator))
            self.assertEqual(1, len(self.calls))

        asyncio.run(run())

    def test_automatic_grant_audits_policy_and_still_rejects_secret(self):
        from personal_assistant.core.models import ContextField, DataClassification, ModelRequest
        from personal_assistant.core.models.automatic import authorize_automatic
        from personal_assistant.core.models.disclosure import DisclosureDeniedError

        async def run():
            request = ModelRequest(
                purpose="fixture",
                instruction="describe",
                fields=(
                    ContextField(
                        "x", "synthetic-private-body", DataClassification.PERSONAL, "user"
                    ),
                ),
            )
            cid = await authorize_automatic(
                request,
                provider_id="remote.openai",
                user_id="owner",
                key="auto",
                disclosures=self.container.disclosures,
                audit=self.container.audit_writer,
            )
            record = await self.container.disclosures.get(cid)
            logs = await self.container.audit_writer.list_events()
            self.assertEqual("model.disclosure.automatic", logs[-1].event_type)
            self.assertEqual("automatic", logs[-1].data["mode"])
            self.assertNotIn("synthetic-private-body", str(logs))
            protected = replace(
                request,
                fields=(replace(request.fields[0], classification=DataClassification.SECRET),),
            )
            with self.assertRaises(DisclosureDeniedError):
                await authorize_automatic(
                    protected,
                    provider_id="remote.openai",
                    user_id="owner",
                    key="blocked",
                    disclosures=self.container.disclosures,
                    audit=self.container.audit_writer,
                )
            self.assertEqual("fixture", record.purpose)

        asyncio.run(run())

    def test_policy_only_profile_still_loads_shared_model_config(self):
        from personal_assistant.infrastructure.operations.host import settings_from_profile

        root = Path(self.enterContext(TemporaryDirectory()))
        model = root / "var/config/model.json"
        model.parent.mkdir(parents=True)
        model.write_text(
            json.dumps(
                {
                    "PA_MODEL_REMOTE_PROVIDER_ID": "fixture",
                    "PA_MODEL_REMOTE_MODEL": "fixture-model",
                    "PA_MODEL_REMOTE_BASE_URL": "https://fixture.example.test",
                    "PA_MODEL_REMOTE_SECRET_HANDLE": "synthetic-handle",
                    "PA_MODEL_REMOTE_JSON_MODE": "true",
                    "PA_MODEL_REMOTE_THINKING": "disabled",
                }
            )
        )
        profile = root / "host.json"
        profile.write_text(json.dumps({"PA_MODEL_DISCLOSURE_MODE": "automatic"}))
        with patch.dict(os.environ, {}, clear=True):
            settings = settings_from_profile(profile, root=root)
        self.assertEqual("automatic", settings.model_disclosure_mode)
        self.assertEqual("fixture-model", settings.model_remote_model)

    def test_automatic_model_policy_still_stops_real_runtime_at_r2_approval(self):
        from personal_assistant.core.models import ModelRouter, RecipientIdentity
        from personal_assistant.core.models.disclosure import DisclosureConsentService
        from personal_assistant.core.tools.gateway import ToolGateway
        from personal_assistant.core.tools.policy import ToolPolicy
        from personal_assistant.domain import RiskLevel, TaskState, ToolDescriptor
        from personal_assistant.infrastructure.memory import InMemoryDisclosureConsentStore

        class Remote:
            provider_id = "remote.openai"
            is_remote = True
            recipient = RecipientIdentity(provider_id, "fake", "https://fixture.test", "fixture")
            calls = 0

            async def complete(self, request):
                self.calls += 1
                return ModelOutput(
                    text=json.dumps(
                        {
                            "kind": "CALL_TOOL",
                            "tool_id": "fixture.external",
                            "tool_version": "1",
                            "arguments": {},
                        }
                    ),
                    provider_id=self.provider_id,
                    model_id="fixture",
                )

        async def run():
            provider = Remote()
            self.container.disclosures = DisclosureConsentService(
                InMemoryDisclosureConsentStore(),
                recipients={provider.provider_id: provider.recipient},
            )
            self.container.model_router = ModelRouter(
                (provider,),
                disclosure=self.container.disclosures,
                audit=self.container.audit_writer,
            )
            descriptor = ToolDescriptor(
                id="fixture.external",
                version="1",
                extension_id="fixture",
                extension_version="1",
                risk=RiskLevel.EXTERNAL_WRITE,
                input_schema={"type": "object", "additionalProperties": False},
                output_schema={"type": "object"},
            )
            self.container.tool_registry.publish((descriptor,))
            executor = AsyncMock()
            runtime = build_runtime(self.container)
            runtime.tools = ToolGateway(
                registry=self.container.tool_registry,
                policy=ToolPolicy(),
                approvals=self.container.approvals,
                executor=executor,
                outbox=self.container.side_effect_outbox,
            )
            runtime.prepare = AsyncMock(
                side_effect=lambda call: replace(call, target={"id": "test"})
            )
            task = await self.container.tasks.create(
                objective="synthetic external action", idempotency_key="r2"
            )
            await runtime.work_once()
            task = await self.container.tasks.get(task.id)
            self.assertEqual(TaskState.WAITING_APPROVAL, task.state)
            self.assertEqual(1, provider.calls)
            executor.execute.assert_not_awaited()
            run = await self.container.run_repository.get("run_" + task.id)
            approval = await self.container.approvals.get(run.waiting_reference)
            self.assertEqual("WAITING_APPROVAL", approval.state.value)

        asyncio.run(run())

    def test_repeated_cancellation_cannot_interrupt_stream_close(self):
        from starlette.requests import Request

        from personal_assistant.api.workbench import ChatStreamRequest, chat_stream

        async def run():
            closing = asyncio.Event()
            release_close = asyncio.Event()
            closed = asyncio.Event()

            class Stream:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    return SimpleNamespace(delta="x", output=None)

                async def aclose(self):
                    closing.set()
                    await release_close.wait()
                    closed.set()

            self.container.model_router.stream = lambda *a, **kw: Stream()
            request = Request({"type": "http", "state": {"idempotency_key": "double-cancel"}})
            response = await chat_stream(
                ChatStreamRequest(message="x"), request, self.container, "local-admin"
            )
            await anext(response.body_iterator)
            finish = asyncio.create_task(response.body_iterator.aclose())
            await asyncio.wait_for(closing.wait(), timeout=2)
            finish.cancel()
            await asyncio.sleep(0)
            finish.cancel()
            release_close.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(finish, timeout=2)
            self.assertTrue(closed.is_set())

        asyncio.run(run())
