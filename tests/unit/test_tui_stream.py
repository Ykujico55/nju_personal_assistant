"""Incremental terminal transport and real headless UI, without original services."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from personal_assistant.cli.tui.service import WorkbenchError, WorkbenchService


def event(kind, value):
    return ("event: " + kind + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode()


DONE = {
    "answer": "你好",
    "model": "fixture",
    "provider": "fixture",
    "usage": {"total_tokens": 3},
    "tools_executed": False,
}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, gate=None, broken=False):
        self.gate, self.broken, self.closed = gate, broken, False

    async def __aiter__(self):
        value = event("delta", {"text": "你"})
        for byte in value:
            yield bytes([byte])
        if self.gate:
            await self.gate.wait()
        if self.broken:
            raise httpx.ReadError("synthetic disconnect")
        yield event("delta", {"text": "好"})
        yield event("completed", DONE)

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_stream_delivers_unicode_chunk_before_receipt_and_releases_connection():
    gate, observed = asyncio.Event(), asyncio.Event()
    stream = Chunks(gate)
    calls, texts = [], []

    async def respond(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async def delta(text):
        texts.append(text)
        observed.set()

    service = WorkbenchService(transport=httpx.MockTransport(respond))
    running = asyncio.create_task(service.chat_stream("question", [], "stream-key", on_delta=delta))
    try:
        await asyncio.wait_for(observed.wait(), 2)
        assert texts == ["你"] and not running.done()
        assert service.pending is not None
        with pytest.raises(WorkbenchError):
            await service.create_task("another", "new-key")
        gate.set()
        assert await asyncio.wait_for(running, 2) == DONE
        assert texts == ["你", "好"]
        assert calls[0].headers["accept"] == "text/event-stream"
        assert service.pending is None and stream.closed
    finally:
        gate.set()
        await asyncio.gather(running, return_exceptions=True)
        await service.aclose()


@pytest.mark.asyncio
async def test_disconnect_retries_original_body_key_receipt_without_duplicate_delta():
    calls, texts = [], []
    stream = Chunks(broken=True)

    async def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=event("completed", DONE)
        )

    async def delta(text):
        texts.append(text)

    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkbenchError) as failure:
            await service.chat_stream("question", [], "same-key", on_delta=delta)
        assert failure.value.uncertain and service.pending and stream.closed
        assert await service.retry_pending() == DONE
        assert texts == ["你"]
        assert calls[0].content == calls[1].content
        assert calls[1].headers["idempotency-key"] == "same-key"
        assert service.pending is None
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        event("delta", {"text": "incomplete"}),
        b"event: completed\ndata: not-json\n\n",
        event("completed", {**DONE, "tools_executed": True}),
        event("delta", {"text": "x" * 8001}),
    ],
)
async def test_truncated_or_malformed_stream_is_not_success(payload):
    async def respond(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload)

    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkbenchError) as failure:
            await service.chat_stream("question", [], "key")
        assert failure.value.uncertain and service.pending is not None
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_stream_terminal_error_clears_pending_but_unknown_keeps_it():
    replies = [
        event("error", {"code": "CHAT_RESPONSE_INVALID", "message": "bad", "uncertain": False}),
        event("error", {"code": "CHAT_RESULT_UNKNOWN", "message": "unknown", "uncertain": True}),
    ]

    async def respond(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=replies.pop(0)
        )

    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkbenchError):
            await service.chat_stream("question", [], "first")
        assert service.pending is None
        with pytest.raises(WorkbenchError):
            await service.chat_stream("question", [], "second")
        assert service.pending
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_cancelled_stream_closes_connection_and_keeps_original_command():
    gate, observed = asyncio.Event(), asyncio.Event()
    stream = Chunks(gate)

    async def respond(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async def delta(text):
        observed.set()

    service = WorkbenchService(transport=httpx.MockTransport(respond))
    running = asyncio.create_task(service.chat_stream("question", [], "cancel-key", on_delta=delta))
    try:
        await asyncio.wait_for(observed.wait(), 2)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert stream.closed and service.pending
    finally:
        gate.set()
        await asyncio.gather(running, return_exceptions=True)
        await service.aclose()


@pytest.mark.asyncio
async def test_automatic_tui_has_no_dialog_renders_partial_and_counts_final_once():
    pytest.importorskip("textual")
    from test_tui_app import FakeService, settled
    from textual.widgets import Input, RichLog

    from personal_assistant.cli.tui.app import ConfirmScreen, WorkbenchApp

    class Streaming(FakeService):
        def __init__(self):
            super().__init__()
            self.gate, self.observed = asyncio.Event(), asyncio.Event()

        async def status(self):
            return {**await super().status(), "model_disclosure_mode": "automatic"}

        async def chat_stream(self, message, history, key, *, preview_hash=None, on_delta=None):
            self.calls.append(("stream", message))
            await on_delta("你好")
            self.observed.set()
            await self.gate.wait()
            return DONE

    service = Streaming()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "question"
        try:
            await pilot.click("#ask")
            await asyncio.wait_for(service.observed.wait(), 2)
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmScreen)
            assert any("你好" in line.text for line in app.query_one("#chat-log", RichLog).lines)
            assert not app.history and not app._usage
            app._start("ask")
            assert service.calls == [("stream", "question")]
        finally:
            service.gate.set()
        await settled(app)
        assert app.history[-1]["content"] == "你好"
        assert app._usage == [{"total_tokens": 3}]
        assert app.query_one("#chat-input", Input).value == ""


@pytest.mark.asyncio
async def test_agent_history_is_sent_directly_with_role_boundaries():
    pytest.importorskip("textual")
    from test_tui_app import FakeService, settled
    from textual.widgets import Input

    from personal_assistant.cli.tui.app import ConfirmScreen, WorkbenchApp

    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.history = [{"role": "assistant", "content": "proposal only"}]
        app.query_one("#chat-input", Input).value = "prepare it"
        await pilot.click("#publish-task")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        await settled(app)
        assert len(service.calls) == 1
        objective = service.calls[0][1][0]
        assert "proposal only" in objective and "不构成新的动作授权" in objective


@pytest.mark.asyncio
async def test_discovered_extension_cannot_enable_and_diagnostic_is_visible():
    pytest.importorskip("textual")
    from test_tui_app import FakeService, settled

    from personal_assistant.cli.tui.app import ConfirmScreen, WorkbenchApp

    class Discovered(FakeService):
        async def extensions(self):
            return [{"id": "fixture.ehall", "version": "0.1.0", "state": "DISCOVERED"}]

    service = Discovered()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.selected_extension_id = "fixture.ehall"
        app.action_tab("extensions-tab")
        await pilot.pause()
        await pilot.click("#extension-enable")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        await settled(app)
        assert not service.calls and "未安装" in app.last_notice
        await app._operation(
            {"id": "extop_fixture", "status": "FAILED", "diagnostic_code": "NOT_INSTALLED"}
        )
        assert "NOT_INSTALLED" in app.last_notice and "安装" in app.last_notice


@pytest.mark.asyncio
async def test_ui_partial_failure_replay_counts_usage_once_and_preserves_edit():
    pytest.importorskip("textual")
    from test_tui_app import FakeService, settled
    from textual.widgets import Input, RichLog

    from personal_assistant.cli.tui.app import WorkbenchApp

    class Interrupted(FakeService):
        async def status(self):
            return {**await super().status(), "model_disclosure_mode": "automatic"}

        async def chat_stream(self, message, history, key, *, preview_hash=None, on_delta=None):
            self.calls.append(("stream", key))
            self.pending = {"retryable": True}
            await on_delta("你")
            raise WorkbenchError("CHAT_RESULT_UNKNOWN", uncertain=True)

        async def retry_pending(self):
            self.calls.append(("retry", None))
            self.pending = None
            return DONE

    service = Interrupted()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "question"
        await pilot.click("#ask")
        await settled(app)
        assert not app.history and not app._usage
        assert "未完成" in app._stream_state
        assert any("你" in line.text for line in app.query_one("#chat-log", RichLog).lines)
        app.query_one("#chat-input", Input).value = "new unsent input"
        await pilot.click("#retry-command")
        await settled(app)
        assert len(app.history) == 2 and app.history[-1]["content"] == DONE["answer"]
        assert app._usage == [DONE["usage"]]
        assert app.query_one("#chat-input", Input).value == "new unsent input"
        assert [kind for kind, _ in service.calls] == ["stream", "retry"]


@pytest.mark.asyncio
async def test_stream_render_does_not_pull_reader_from_history():
    pytest.importorskip("textual")
    from test_tui_app import FakeService, settled
    from textual.widgets import RichLog

    from personal_assistant.cli.tui.app import WorkbenchApp

    app = WorkbenchApp(FakeService(), refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.history = [{"role": "assistant", "content": "history line\n\n" * 70}]
        app._stream_question, app._stream_text, app._stream_state = "question", "first", "正在回复"
        app._render_chat()
        await pilot.pause()
        log = app.query_one("#chat-log", RichLog)
        assert log.max_scroll_y > 0
        log.scroll_to(y=0, animate=False, immediate=True, force=True)
        await pilot.pause()
        app._stream_text += " second"
        app._render_chat()
        await pilot.pause()
        assert log.scroll_y == 0


@pytest.mark.asyncio
async def test_large_completed_reply_survives_old_wire_limit_with_extended_deadline(monkeypatch):
    from personal_assistant.cli.tui import service as service_module

    budgets = []
    timeout = asyncio.timeout

    def measured_timeout(seconds):
        budgets.append(seconds)
        return timeout(min(seconds, 5))

    monkeypatch.setattr(service_module.asyncio, "timeout", measured_timeout)
    answer = "完整回答" * 100000
    payload = event("delta", {"text": answer}) + event("completed", {**DONE, "answer": answer})
    assert len(payload) > 2_097_152

    async def respond(request):
        assert request.extensions["timeout"] == {
            "connect": 15, "read": 690, "write": 30, "pool": 15,
        }
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload)

    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        result = await service.conversation_stream("question", [], "large-reply")
        assert result["answer"] == answer and service.pending is None
        assert budgets[0] == 690
    finally:
        await service.aclose()
