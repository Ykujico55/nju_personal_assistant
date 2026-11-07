"""Typed model termination through real HTTP adapter, API and terminal client.

Synthetic transport and isolated runtime directories only; no vendor/network/original state.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from personal_assistant.admin_app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.cli.tui.service import WorkbenchError, WorkbenchService
from personal_assistant.core.models import ModelOutput
from personal_assistant.infrastructure.memory.secrets import InMemorySecretStore
from personal_assistant.infrastructure.models.openai_compatible import (
    OpenAICompatibleChatProvider,
    OpenAICompatibleConfig,
)
from personal_assistant.settings import Settings
from tests.unit.test_model_streaming import Stream, chunk


@asynccontextmanager
async def scenario(tmp_path, case, phase="answer", fail_receipt=False):
    calls, deltas = [], []
    ending = {"length": "length", "filter": "content_filter"}.get(case, "stop")
    parts = [chunk("synthetic partial"), chunk(finish=ending), b"data: [DONE]\n\n"]
    if case == "eof":
        parts.pop()
    class Wire(Stream):
        async def __aiter__(self):
            async for piece in super().__aiter__():
                yield piece
                if self.read == 1 and case in {"network", "timeout"}:
                    error = httpx.ReadTimeout if case == "timeout" else httpx.ReadError
                    raise error("private synthetic transport detail")
    wire = Wire(parts)
    async def network(request):
        calls.append(request.method)
        status = {"rejected": 401, "rate_limit": 429}.get(case, 200)
        if phase == "route" and case in {"length", "filter"}:
            return httpx.Response(200, json={"choices": [{"message": {"content": "partial"},
                "finish_reason": ending}]})
        return httpx.Response(status, stream=wire, headers={"Content-Type": "text/event-stream"})
    secrets = InMemorySecretStore()
    handle = await secrets.put(name="fixture", kind="model_api_key", value="test-only-key")
    client = httpx.AsyncClient(transport=httpx.MockTransport(network))
    adapter = OpenAICompatibleChatProvider(OpenAICompatibleConfig(provider_id="remote.openai",
        model_id="fixture", base_url="https://synthetic.invalid", secret_handle=handle),
        secret_store=secrets, http_client=client)
    class Provider:
        provider_id, is_remote, recipient = adapter.provider_id, True, adapter.recipient
        route_calls = 0
        async def complete(self, request):
            self.route_calls += 1
            if phase == "route":
                return await adapter.complete(request)
            return ModelOutput(text='{"kind":"ANSWER","query":"","providers":[]}',
                provider_id=self.provider_id, model_id="fixture", usage={"total_tokens": 3})
        def stream(self, request):
            return adapter.stream(request)
    provider = Provider()
    with patch.dict("os.environ", {}, clear=True):
        settings = replace(Settings.from_env(), extension_root=tmp_path / "extensions",
            artifact_root=tmp_path / "artifacts", model_disclosure_mode="automatic")
    with patch("personal_assistant.bootstrap._build_model_providers", return_value=[provider]):
        container = build_container(settings, secret_store=secrets)
    if case == "credential":
        async def unavailable(*args, **kwargs):
            raise ValueError("private broker detail must not escape")
        secrets.resolve_for_broker = unavailable
    original = container.records.put
    if fail_receipt:
        async def failed_put(bucket, key, value, **kwargs):
            if value.get("state") == "FAILED":
                raise RuntimeError("private receipt store detail")
            return await original(bucket, key, value, **kwargs)
        container.records.put = failed_put
    service = WorkbenchService(transport=httpx.ASGITransport(
        app=create_app(settings=settings, container=container), client=("127.0.0.1", 12345)))
    async def on_delta(value):
        deltas.append(value)
    try:
        yield SimpleNamespace(service=service, container=container, provider=provider,
            calls=calls, wire=wire, deltas=deltas, on_delta=on_delta)
    finally:
        await service.aclose()
        await container.aclose()
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("case,phase,code", [
    ("length", "answer", "MODEL_OUTPUT_TRUNCATED"),
    ("filter", "answer", "MODEL_OUTPUT_FILTERED"),
    ("rejected", "answer", "MODEL_PROVIDER_REJECTED"),
    ("rate_limit", "answer", "MODEL_PROVIDER_REJECTED"),
    ("credential", "answer", "MODEL_CREDENTIAL_UNAVAILABLE"),
    ("length", "route", "MODEL_OUTPUT_TRUNCATED"),
    ("filter", "route", "MODEL_OUTPUT_FILTERED"),
    ("rejected", "route", "MODEL_PROVIDER_REJECTED"),
    ("credential", "route", "MODEL_CREDENTIAL_UNAVAILABLE"),
])
async def test_confirmed_failure_is_durable_and_replay_does_not_invoke_again(
    tmp_path, case, phase, code,
):
    async with scenario(tmp_path, case, phase) as env:
        with pytest.raises(WorkbenchError) as first:
            await env.service.conversation_stream("question", [], "same", on_delta=env.on_delta)
        assert first.value.code == code and first.value.uncertain is False
        assert env.service.pending is None
        assert "private" not in first.value.message
        records = await env.container.records.list("workbench-conversation")
        assert records[0]["value"]["state"] == "FAILED"
        assert records[0]["value"]["error_code"] == code
        assert not await env.container.tasks.list_recent(limit=20)
        calls_before = (len(env.calls), env.provider.route_calls)
        with pytest.raises(WorkbenchError) as replay:
            await env.service.conversation_stream("question", [], "same")
        assert replay.value.code == code and replay.value.uncertain is False
        assert (len(env.calls), env.provider.route_calls) == calls_before
        assert env.service.pending is None
        assert len(env.deltas) == (1 if case in {"length", "filter"} and phase == "answer" else 0)
        assert phase == "route" or not env.calls or env.wire.closed
        # Explicitly submitting another request is allowed; no automatic retry occurred.
        with pytest.raises(WorkbenchError):
            await env.service.conversation_stream("new question", [], "another")
        assert env.provider.route_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["eof", "network", "timeout"])
async def test_unconfirmed_transport_failure_remains_unknown_and_keeps_pending(tmp_path, case):
    async with scenario(tmp_path, case) as env:
        with pytest.raises(WorkbenchError) as first:
            await env.service.conversation_stream("question", [], "same", on_delta=env.on_delta)
        assert first.value.code == "CHAT_RESULT_UNKNOWN" and first.value.uncertain
        assert env.service.pending is not None and env.wire.closed
        records = await env.container.records.list("workbench-conversation")
        assert records[0]["value"]["state"] == "STREAMING"
        with pytest.raises(WorkbenchError) as replay:
            await env.service.retry_pending()
        assert replay.value.uncertain and len(env.calls) == 1
        assert env.provider.route_calls == 1
        assert not await env.container.tasks.list_recent(limit=20)


@pytest.mark.asyncio
async def test_failure_receipt_write_error_stays_unknown(tmp_path):
    async with scenario(tmp_path, "length", fail_receipt=True) as env:
        with pytest.raises(WorkbenchError) as first:
            await env.service.conversation_stream("question", [], "same")
        assert first.value.code == "CHAT_RESULT_UNKNOWN" and first.value.uncertain
        assert env.service.pending is not None
        records = await env.container.records.list("workbench-conversation")
        assert records[0]["value"]["state"] == "STREAMING"
        with pytest.raises(WorkbenchError):
            await env.service.retry_pending()
        assert len(env.calls) == 1 and env.provider.route_calls == 1


@pytest.mark.asyncio
async def test_discard_reply_preserves_original_receipt_and_rejects_late_success(tmp_path):
    from personal_assistant.domain import ConcurrentModificationError
    async with scenario(tmp_path, "eof") as env:
        with pytest.raises(WorkbenchError):
            await env.service.conversation_stream("question", [], "same", on_delta=env.on_delta)
        assert env.service.pending
        records = await env.container.records.list("workbench-conversation")
        key = records[0]["id"]
        with pytest.raises(WorkbenchError) as discarded:
            await env.service.discard_pending_reply()
        assert discarded.value.code == "CHAT_RESPONSE_DISCARDED"
        assert discarded.value.uncertain is False and env.service.pending is None
        record = await env.container.records.get("workbench-conversation", key)
        assert record["value"] == {"state": "FAILED", "error_code": "CHAT_RESPONSE_DISCARDED"}
        with pytest.raises(ConcurrentModificationError):
            await env.container.records.put("workbench-conversation", key,
                {"state": "SUCCEEDED", "response": {"answer": "late"}}, expected_version=2)
        with pytest.raises(WorkbenchError) as replay:
            await env.service.conversation_stream("question", [], "same")
        assert replay.value.code == "CHAT_RESPONSE_DISCARDED"
        assert len(env.calls) == 1 and env.provider.route_calls == 1
        assert len(env.deltas) == 1 and not await env.container.tasks.list_recent(limit=20)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["DISPATCHING", "READY_TASK"])
async def test_discard_rejects_commands_that_may_create_tasks(tmp_path, state):
    async with scenario(tmp_path, "eof") as env:
        with pytest.raises(WorkbenchError):
            await env.service.conversation_stream("question", [], "same")
        record = (await env.container.records.list("workbench-conversation"))[0]
        await env.container.records.put("workbench-conversation", record["id"],
            {"state": state}, expected_version=record["version"])
        with pytest.raises(WorkbenchError) as denied:
            await env.service.discard_pending_reply()
        assert denied.value.code == "CHAT_DISCARD_NOT_ALLOWED" and denied.value.uncertain
        assert env.service.pending is not None
        current = await env.container.records.get("workbench-conversation", record["id"])
        assert current["value"]["state"] == state
        assert env.provider.route_calls == 1 and len(env.calls) == 1


@pytest.mark.asyncio
async def test_discard_returns_existing_success_receipt_without_overwriting(tmp_path):
    async with scenario(tmp_path, "eof") as env:
        with pytest.raises(WorkbenchError):
            await env.service.conversation_stream("question", [], "same")
        record = (await env.container.records.list("workbench-conversation"))[0]
        response = {"answer": "confirmed original", "model": "fixture", "provider": "fixture",
                    "tools_executed": False, "usage": {"total_tokens": 12}}
        await env.container.records.put("workbench-conversation", record["id"],
            {"state": "SUCCEEDED", "response": response}, expected_version=record["version"])
        assert await env.service.discard_pending_reply() == response
        assert env.service.pending is None and env.provider.route_calls == 1


@pytest.mark.asyncio
async def test_discard_checks_original_request_fingerprint(tmp_path):
    async with scenario(tmp_path, "eof") as env:
        with pytest.raises(WorkbenchError):
            await env.service.conversation_stream("question", [], "same")
        response = await env.service._client.post("/admin/v1/workbench/conversation/discard",
            json={"message": "different", "history": []}, headers={"Idempotency-Key": "same"})
        assert response.status_code == 409
        records = await env.container.records.list("workbench-conversation")
        assert records[0]["value"]["state"] == "STREAMING"


@pytest.mark.asyncio
async def test_concurrent_success_during_discard_returns_success(tmp_path):
    async with scenario(tmp_path, "eof") as env:
        with pytest.raises(WorkbenchError):
            await env.service.conversation_stream("question", [], "same")
        original = env.container.records.put
        response = {"answer": "concurrent confirmed answer", "model": "fixture",
                    "tools_executed": False, "usage": {"total_tokens": 12}}
        async def race(bucket, key, value, **kwargs):
            if value.get("error_code") == "CHAT_RESPONSE_DISCARDED":
                await original(bucket, key, {"state": "SUCCEEDED", "response": response},
                               expected_version=2)
            return await original(bucket, key, value, **kwargs)
        env.container.records.put = race
        assert await env.service.discard_pending_reply() == response
        assert env.service.pending is None and len(env.calls) == 1


@pytest.mark.asyncio
async def test_uncertain_discard_keeps_original_stream_request(tmp_path):
    async with scenario(tmp_path, "eof") as env:
        with pytest.raises(WorkbenchError):
            await env.service.conversation_stream("question", [], "same")
        original = env.container.records.put
        async def failure(bucket, key, value, **kwargs):
            if value.get("error_code") == "CHAT_RESPONSE_DISCARDED":
                raise RuntimeError("synthetic store unavailable")
            return await original(bucket, key, value, **kwargs)
        env.container.records.put = failure
        with pytest.raises(WorkbenchError) as discarded:
            await env.service.discard_pending_reply()
        assert discarded.value.uncertain
        assert env.service.pending["path"].endswith("/conversation/stream")
        with pytest.raises(WorkbenchError):
            await env.service.retry_pending()
        assert len(env.calls) == 1 and env.provider.route_calls == 1
