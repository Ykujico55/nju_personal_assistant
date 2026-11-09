import httpx
import pytest

from personal_assistant.cli.tui.service import WorkbenchError, WorkbenchService


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_server_failure_keeps_safe_correlation_without_error_body(stream):
    identifier = "req_" + "a" * 32

    async def response(request):
        return httpx.Response(
            503,
            headers={"X-Request-ID": identifier},
            json={
                "error": {
                    "code": "MODEL_CREDENTIAL_UNAVAILABLE",
                    "request_id": identifier,
                    "message": "synthetic-private-secret must not be displayed",
                    "details": {"raw": "secret"},
                }
            },
        )

    service = WorkbenchService(transport=httpx.MockTransport(response))
    try:
        with pytest.raises(WorkbenchError) as failure:
            if stream:
                await service.conversation_stream("public", [], "key")
            else:
                await service.status()
        assert failure.value.correlation_id == identifier
        assert "synthetic-private-secret" not in failure.value.message
        assert "凭据" in failure.value.message
    finally:
        await service.aclose()


def test_error_rejects_untrusted_correlation_text():
    error = WorkbenchError("FAILED", correlation_id="private/secret/path")
    assert error.correlation_id is None


@pytest.mark.asyncio
async def test_unknown_stream_retry_preserves_diagnostic_and_original_request():
    from test_tui_stream import event

    original_id = "req_" + "a" * 32
    current_id = "req_" + "b" * 32
    diagnostic = {
        "reason_code": "MODEL_TIMEOUT", "stage": "ANSWER", "request_id": original_id,
        "elapsed_ms": 1234, "persisted": True,
    }
    calls, texts, failures = [], [], []

    async def response(request):
        calls.append(request)
        frames = event("delta", {"text": "incomplete"}) if len(calls) == 1 else b""
        return httpx.Response(
            200, headers={
                "content-type": "text/event-stream",
                "X-Request-ID": original_id if len(calls) == 1 else current_id,
            },
            content=frames + event("error", {
                "code": "CHAT_RESULT_UNKNOWN", "uncertain": True,
                "message": "synthetic-private-secret", "diagnostic": diagnostic,
            }),
        )

    async def delta(value):
        texts.append(value)

    service = WorkbenchService(transport=httpx.MockTransport(response))
    try:
        for attempt in range(2):
            with pytest.raises(WorkbenchError) as failure:
                if attempt == 0:
                    await service.conversation_stream(
                        "original question", [], "same-original-key", on_delta=delta
                    )
                else:
                    await service.retry_pending()
            failures.append(failure.value)
            assert failure.value.code == "CHAT_RESULT_UNKNOWN"
            assert failure.value.uncertain and service.pending
            assert failure.value.diagnostic == diagnostic
            assert "synthetic-private-secret" not in failure.value.message
        assert failures[0].correlation_id == original_id
        assert failures[1].correlation_id == current_id
        assert texts == ["incomplete"]
        assert calls[0].content == calls[1].content
        assert all(call.headers["idempotency-key"] == "same-original-key" for call in calls)
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnostic", [
    None,
    {"reason_code": "synthetic-private-secret", "stage": "ANSWER", "request_id": None,
     "elapsed_ms": 1, "persisted": True},
    {"reason_code": "MODEL_TIMEOUT", "stage": "synthetic-private-secret", "request_id": None,
     "elapsed_ms": 1, "persisted": True},
    {"reason_code": "MODEL_TIMEOUT", "stage": "ANSWER", "request_id": "private/secret/path",
     "elapsed_ms": 1, "persisted": True},
    {"reason_code": "MODEL_TIMEOUT", "stage": "ANSWER", "request_id": None,
     "elapsed_ms": True, "persisted": True},
    {"reason_code": "MODEL_TIMEOUT", "stage": "ANSWER", "request_id": None,
     "elapsed_ms": 1, "persisted": True, "message": "synthetic-private-secret"},
])
async def test_unknown_stream_ignores_missing_or_untrusted_diagnostic(diagnostic):
    from test_tui_stream import event

    async def response(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=event("error", {
                "code": "CHAT_RESULT_UNKNOWN", "uncertain": True,
                "message": "synthetic-private-secret", "diagnostic": diagnostic,
            }),
        )

    service = WorkbenchService(transport=httpx.MockTransport(response))
    try:
        with pytest.raises(WorkbenchError) as failure:
            await service.conversation_stream("original question", [], "key")
        assert failure.value.diagnostic is None
        assert failure.value.message == "回复结果未确认，请重试原请求。"
        assert failure.value.uncertain and service.pending
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_tui_unknown_diagnostic_keeps_input_and_distinguishes_retry_reference():
    pytest.importorskip("textual")
    from test_tui_app import FakeService, settled
    from textual.widgets import Input, RichLog

    from personal_assistant.cli.tui.app import WorkbenchApp
    from personal_assistant.core.conversation_diagnostics import diagnostic_message

    original_id = "req_" + "a" * 32
    current_id = "req_" + "b" * 32
    diagnostic = {
        "reason_code": "MODEL_TIMEOUT", "stage": "ANSWER", "request_id": original_id,
        "elapsed_ms": 1234, "persisted": True,
    }

    class Interrupted(FakeService):
        async def status(self):
            return {**await super().status(), "model_disclosure_mode": "automatic"}

        async def conversation_stream(self, message, history, key, *, on_delta=None):
            self.calls.append(("conversation", key))
            self.pending = {"retryable": True}
            await on_delta("incomplete")
            raise WorkbenchError("CHAT_RESULT_UNKNOWN", uncertain=True,
                                 correlation_id=original_id, diagnostic=diagnostic)

        async def retry_pending(self):
            self.calls.append(("retry", None))
            raise WorkbenchError("CHAT_RESULT_UNKNOWN", uncertain=True,
                                 correlation_id=current_id, diagnostic=diagnostic)

    service = Interrupted()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "original question"
        await pilot.click("#ask")
        await settled(app)
        assert diagnostic_message(diagnostic) in app.last_notice
        assert f"原故障编号 {original_id}" in app.last_notice
        assert app.query_one("#chat-input", Input).value == "original question"
        assert not app.history and not app._usage
        app.query_one("#chat-input", Input).value = "new unsent input"
        await pilot.click("#retry-command")
        await settled(app)
        assert diagnostic_message(diagnostic) in app.last_notice
        assert f"原故障编号 {original_id}" in app.last_notice
        assert f"本次请求编号 {current_id}" in app.last_notice
        assert diagnostic_message(diagnostic) in app._stream_state
        assert f"原故障编号 {original_id}" in app._stream_state
        assert f"本次请求编号 {current_id}" in app._stream_state
        rendered = "".join(line.text for line in app.query_one("#chat-log", RichLog).lines)
        assert "模型回复超过本次调用时限" in rendered
        assert original_id in rendered and current_id in rendered
        assert app.query_one("#chat-input", Input).value == "new unsent input"
        assert service.pending and not app.history and not app._usage
        assert app._stream_text == "incomplete"
        assert [name for name, _ in service.calls] == ["conversation", "retry"]


@pytest.mark.asyncio
async def test_http_unknown_response_keeps_current_header_id_separate_from_original_diagnostic():
    original_id, current_id = "req_" + "a" * 32, "req_" + "b" * 32
    diagnostic = {
        "reason_code": "RECEIPT_WRITE_FAILED", "stage": "RECEIPT", "request_id": original_id,
        "elapsed_ms": 1234, "persisted": False,
    }

    async def response(request):
        return httpx.Response(503, headers={"X-Request-ID": current_id}, json={
            "error": {"code": "CHAT_RESULT_UNKNOWN", "request_id": original_id,
                      "message": "synthetic-private-secret", "diagnostic": diagnostic},
        })

    service = WorkbenchService(transport=httpx.MockTransport(response))
    try:
        with pytest.raises(WorkbenchError) as failure:
            await service.conversation_stream("original question", [], "key")
        assert failure.value.correlation_id == current_id
        assert failure.value.diagnostic == diagnostic
        assert failure.value.uncertain and service.pending
        assert "synthetic-private-secret" not in failure.value.message
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_invalid_route_diagnostic_is_visible_without_unknown_retry():
    from test_tui_stream import event

    diagnostic = {
        "reason_code": "ROUTE_PROVIDER_UNAVAILABLE", "stage": "ROUTE",
        "request_id": "req_" + "a" * 32, "elapsed_ms": 123, "persisted": True,
    }

    async def response(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content=event("error", {"code": "CHAT_RESPONSE_INVALID", "uncertain": False,
                "message": "private arbitrary model text", "diagnostic": diagnostic}))

    service = WorkbenchService(transport=httpx.MockTransport(response))
    try:
        with pytest.raises(WorkbenchError) as failure:
            await service.conversation_stream("public question", [], "key")
        assert failure.value.diagnostic == diagnostic
        assert not failure.value.uncertain and not service.pending
        assert "private" not in failure.value.message
    finally:
        await service.aclose()
