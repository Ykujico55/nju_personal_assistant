"""Terminal reply failures and explicit abandonment never fabricate a completed answer."""
from __future__ import annotations

import pytest
from test_tui_app import FakeService, settled
from textual.widgets import Input, RichLog

from personal_assistant.cli.tui.app import WorkbenchApp
from personal_assistant.cli.tui.service import PREFIX, WorkbenchError


class InterruptedService(FakeService):
    code = "CHAT_RESULT_UNKNOWN"
    uncertain = True
    async def status(self):
        return {**await super().status(), "model_disclosure_mode": "automatic"}
    async def conversation_stream(self, message, history, key, *, on_delta=None):
        self.calls.append(("conversation", key))
        self.pending = ({"path": PREFIX + "/conversation/stream", "retryable": True}
                        if self.uncertain else None)
        await on_delta("保留的未完成片段")
        raise WorkbenchError(self.code, "fixed failure", uncertain=self.uncertain)
    async def discard_pending_reply(self):
        self.calls.append(("discard", None))
        self.pending = None
        raise WorkbenchError("CHAT_RESPONSE_DISCARDED", "已放弃未完成回复", uncertain=False)


@pytest.mark.asyncio
async def test_definitive_failure_keeps_partial_without_blocking_new_input():
    service = InterruptedService()
    service.code, service.uncertain = "MODEL_OUTPUT_TRUNCATED", False
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "question"
        await pilot.click("#ask")
        await settled(app)
        assert "回复已停止" in app._stream_state
        assert not app.history and not app._usage
        assert app.query_one("#chat-input", Input).value == "question"
        assert not app.query_one("#ask").disabled and not service.pending
        assert any("保留的未完成片段" in line.text
                   for line in app.query_one("#chat-log", RichLog).lines)


@pytest.mark.asyncio
async def test_explicit_discard_unlocks_but_preserves_unsent_input_and_partial():
    service = InterruptedService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "question"
        await pilot.click("#ask")
        await settled(app)
        assert app.query_one("#ask").disabled
        app.query_one("#chat-input", Input).value = "new unsent text"
        await pilot.click("#discard-reply")
        await settled(app)
        assert not service.pending and not app.query_one("#ask").disabled
        assert app.query_one("#chat-input", Input).value == "new unsent text"
        assert not app.history and not app._usage
        assert "已放弃" in app.last_notice
        assert app._stream_text == "保留的未完成片段"
        assert [name for name, _ in service.calls] == ["conversation", "discard"]


@pytest.mark.asyncio
async def test_other_pending_commands_cannot_be_discarded():
    import httpx

    from personal_assistant.cli.tui.service import WorkbenchService
    calls = []
    async def respond(request):
        calls.append(request.url.path)
        return httpx.Response(503, json={"error": {"code": "UNAVAILABLE"}})
    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkbenchError):
            await service.create_task("synthetic", "task-create")
        with pytest.raises(WorkbenchError) as failure:
            await service.discard_pending_reply()
        assert failure.value.code == "CHAT_DISCARD_NOT_ALLOWED" and failure.value.uncertain
        assert service.pending["path"].endswith("/tasks")
        assert calls == [PREFIX + "/tasks"]
    finally:
        await service.aclose()
