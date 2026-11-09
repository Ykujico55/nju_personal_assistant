"""CORE.OUTPUT.1: no presentation/token truncation; input and wire budgets remain."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from test_tui_app import FakeService, settled
from test_tui_stream import DONE, event
from textual.widgets import Input

from personal_assistant.api.workbench import ChatRequest
from personal_assistant.api.workbench_conversation import ConversationRequest
from personal_assistant.cli.tui.app import WorkbenchApp
from personal_assistant.cli.tui.service import WorkbenchError, WorkbenchService
from personal_assistant.core.models import ModelOutput, ModelRequest
from personal_assistant.infrastructure.memory import InMemorySecretStore
from personal_assistant.infrastructure.models import (
    OllamaChatProvider,
    OllamaConfig,
    OpenAICompatibleChatProvider,
    OpenAICompatibleConfig,
)
from personal_assistant.settings import ConfigurationError
from tests.api import test_workbench_stream as fixture

LONG = "完整回答" * 2250


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["remote", "local"])
@pytest.mark.parametrize("limit", ["default", None, 2048])
async def test_output_budget_is_omitted_by_default_and_explicit_limit_is_sent(kind, limit):
    store, sent = InMemorySecretStore(), []
    handle = await store.put(name="fixture", kind="model_api_key", value="test-only-key")
    async def respond(request):
        sent.append(json.loads(request.content))
        value = ({"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]}
                 if kind == "remote" else {"done": True, "message": {"content": "answer"}})
        return httpx.Response(200, json=value)
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    kwargs = {} if limit == "default" else {"max_output_tokens": limit}
    try:
        if kind == "remote":
            provider = OpenAICompatibleChatProvider(OpenAICompatibleConfig(
                provider_id="remote.fixture", model_id="fixture", base_url="https://fixture.invalid",
                secret_handle=handle, **kwargs), secret_store=store, http_client=client)
        else:
            provider = OllamaChatProvider(OllamaConfig(provider_id="local.fixture",
                model_id="fixture", **kwargs), http_client=client)
        output = await provider.complete(ModelRequest(purpose="test", instruction="answer"))
        assert output.text == "answer"
        budget = (sent[0].get("max_tokens") if kind == "remote"
                  else sent[0].get("options", {}).get("num_predict"))
        assert budget == (2048 if limit == 2048 else None)
        if limit != 2048:
            assert "max_tokens" not in sent[0]
            assert "num_predict" not in sent[0].get("options", {})
    finally:
        await client.aclose()


@pytest.mark.parametrize("schema", [ChatRequest, ConversationRequest])
def test_history_keeps_full_assistant_reply_but_user_and_total_budgets_still_apply(schema):
    value = schema(message="follow up", history=[{"role": "assistant", "content": LONG}])
    assert len(value.history[0].content) == len(LONG)
    with pytest.raises(ValidationError):
        schema(message="x" * 8001)
    with pytest.raises(ValidationError):
        schema(message="question", history=[{"role": "user", "content": "x" * 8001}])
    with pytest.raises(ValidationError):
        schema(message="follow up", history=[{"role": "assistant", "content": "x" * 64000}])


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["chat_stream", "conversation_stream"])
async def test_terminal_transport_keeps_long_answer_and_deltas(method):
    seen = []
    wire = b"".join(event("delta", {"text": LONG[i:i+2000]}) for i in range(0, len(LONG), 2000))
    wire += event("completed", {**DONE, "answer": LONG})
    async def respond(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=wire)
    async def delta(value):
        seen.append(value)
    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        result = await getattr(service, method)("question", [], "long", on_delta=delta)
        assert len(result["answer"]) == len(LONG)
        assert "".join(seen) == LONG and service.pending is None
    finally:
        await service.aclose()


class LongAnswerAPITests(unittest.TestCase):
    setUp = fixture.WorkbenchStreamTests.setUp
    def stream_provider(self):
        async def stream(request, **kwargs):
            self.calls.append(request)
            yield SimpleNamespace(delta=LONG[:6000], output=None)
            yield SimpleNamespace(delta=LONG[6000:], output=None)
            yield SimpleNamespace(delta="", output=ModelOutput(text=LONG,
                provider_id="remote.openai", model_id="fixture", usage={"total_tokens": 3}))
        self.container.model_router.stream = stream
    def test_both_streaming_routes_complete_full_answer_and_replay(self):
        self.stream_provider()
        self.container.model_router.complete = AsyncMock(return_value=ModelOutput(
            text='{"kind":"ANSWER","query":"","providers":[]}',
            provider_id="remote.openai", model_id="fixture", usage={"total_tokens": 2}))
        for route in ("chat", "conversation"):
            for _ in range(2):
                response = self.client.post(fixture.PREFIX + f"/{route}/stream",
                    json={"message": "question"}, headers={"Idempotency-Key": route})
                received = fixture.events(response)
                self.assertEqual("completed", received[-1][0])
                self.assertEqual(len(LONG), len(received[-1][1]["answer"]))
            self.assertNotIn("8000", self.calls[-1].instruction)
        self.assertEqual(2, len(self.calls))
    def test_nonstream_chat_confirm_returns_full_answer(self):
        preview = self.client.post(fixture.PREFIX + "/chat/preview", json={"message": "question"},
            headers={"Idempotency-Key": "preview"}).json()
        self.container.model_router.complete = AsyncMock(return_value=ModelOutput(
            text=json.dumps({"answer": LONG}), provider_id="remote.openai", model_id="fixture"))
        response = self.client.post(fixture.PREFIX + "/chat/confirm",
            json={"message": "question", "preview_hash": preview["preview_hash"]},
            headers={"Idempotency-Key": "confirm"})
        self.assertEqual(200, response.status_code)
        self.assertEqual(len(LONG), len(response.json()["answer"]))


class LongReplyService(FakeService):
    async def status(self):
        return {**await super().status(), "model_disclosure_mode": "automatic"}
    async def conversation_stream(self, message, history, key, *, on_delta=None):
        self.calls.append(("conversation", [dict(item) for item in history]))
        await on_delta(LONG)
        return {**DONE, "answer": LONG}


@pytest.mark.asyncio
async def test_tui_keeps_long_reply_for_next_turn():
    service = LongReplyService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        for message in ("question", "follow up"):
            app.query_one("#chat-input", Input).value = message
            await pilot.click("#ask")
            await settled(app)
        assert len(app.history[1]["content"]) == len(LONG)
        assert service.calls[1][1][1]["content"] == LONG


@pytest.mark.asyncio
async def test_tui_rejects_oversized_history_without_silent_slicing():
    service = LongReplyService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        app.history = [{"role": "user", "content": "must preserve instruction"}] + [
            {"role": "assistant", "content": "x" * 5000} for _ in range(13)]
        app.query_one("#chat-input", Input).value = "follow up"
        await pilot.click("#ask")
        await settled(app)
        assert not service.calls
        assert "64000" in app.last_notice and "新对话" in app.last_notice
        assert len(app.history) == 14
        assert app.query_one("#chat-input", Input).value == "follow up"


@pytest.mark.parametrize("limit", [True, 0, -1, 1.5, 32769])
def test_explicit_token_budget_requires_valid_positive_integer(limit):
    with pytest.raises(ConfigurationError):
        OllamaChatProvider(OllamaConfig(provider_id="local.fixture", model_id="fixture",
                                      max_output_tokens=limit))


class SplitWire(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed, self.read = chunks, False, 0
    async def __aiter__(self):
        for piece in self.chunks:
            self.read += 1
            yield piece
    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["chat_stream", "conversation_stream"])
@pytest.mark.parametrize("ascii_json", [False, True])
async def test_chunked_terminal_larger_than_64k_preserves_answer(method, ascii_json):
    answer = "汉" * 25000
    deltas = b"".join(event("delta", {"text": answer[i:i+2000]})
                       for i in range(0, len(answer), 2000))
    terminal = ("event: completed\ndata: " + json.dumps({**DONE, "answer": answer},
                ensure_ascii=ascii_json) + "\n\n").encode()
    assert 65536 < len(terminal) < 2_097_152
    payload = deltas + terminal
    wire = SplitWire([payload[i:i+1024] for i in range(0, len(payload), 1024)])
    async def respond(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=wire)
    seen = []
    async def delta(value):
        seen.append(value)
    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        result = await getattr(service, method)("question", [], "chunked", on_delta=delta)
        assert len(result["answer"]) == len(answer)
        assert "".join(seen) == answer and service.pending is None
        assert wire.read == len(wire.chunks) and wire.closed
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_chunked_stream_total_byte_budget_remains_enforced():
    wire = SplitWire([b": " + b"x" * 65530 + b"\n\n"] * 300)
    async def respond(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=wire)
    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkbenchError) as error:
            await service.conversation_stream("question", [], "too-large")
        assert error.value.uncertain and service.pending is not None
        assert wire.read < len(wire.chunks) and wire.closed
    finally:
        await service.aclose()
