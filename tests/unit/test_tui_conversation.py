"""Conversation dispatch UI and transport; no original services or model calls."""
from __future__ import annotations

import json

import httpx
import pytest
from test_tui_stream import DONE, Chunks, event

from personal_assistant.cli.tui.service import WorkbenchError, WorkbenchService


@pytest.mark.asyncio
async def test_conversation_is_incremental_and_keeps_route_on_receipt_retry():
    calls, deltas = [], []
    receipt = {**DONE, "task": {"id": "task_chat", "state": "QUEUED"}}
    async def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=Chunks(broken=True))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=event("completed", receipt))
    async def delta(value):
        deltas.append(value)
    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkbenchError) as failed:
            await service.conversation_stream("整理计划", [], "same", on_delta=delta)
        assert failed.value.uncertain and service.pending
        assert deltas == ["你"]
        assert await service.retry_pending() == receipt
        assert service.pending is None
        assert len(calls) == 2 and calls[0].content == calls[1].content
        assert all(r.url.path.endswith("/conversation/stream") for r in calls)
        assert all(r.headers["idempotency-key"] == "same" for r in calls)
        assert json.loads(calls[0].content)["message"] == "整理计划"
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"task": {"id": "", "state": "QUEUED"}},
    {"task": {"id": "task_x", "state": 7}},
    {"sources": "not-a-list"},
    {"warnings": [123]},
])
async def test_invalid_conversation_receipt_retains_original_request(extra):
    async def respond(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=event("completed", {**DONE, **extra}))
    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkbenchError) as failed:
            await service.conversation_stream("question", [], "one")
        assert failed.value.uncertain and service.pending
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_task_receipt_stays_in_chat_and_live_state_refreshes():
    pytest.importorskip("textual")
    from test_tui_app import FakeService, settled
    from textual.widgets import Input, RichLog, Static, TabbedContent

    from personal_assistant.cli.tui.app import WorkbenchApp
    class Service(FakeService):
        task_state = "QUEUED"
        async def status(self):
            return {**await super().status(), "model_disclosure_mode": "automatic"}
        async def conversation_stream(self, message, history, key, *, on_delta=None):
            self.calls.append(("conversation", message))
            return {**DONE, "answer": "已创建任务，等待执行。",
                    "task": {"id": "task_chat", "state": "QUEUED"}}
        async def task(self, task_id):
            value = await super().task(task_id)
            value["task"]["state"] = self.task_state
            return value
    service = Service()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(120, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "帮我整理计划"
        await pilot.click("#ask")
        await settled(app)
        assert service.calls == [("conversation", "帮我整理计划")]
        assert app.selected_task_id == "task_chat"
        assert app.query_one("#workbench-tabs", TabbedContent).active == "chat-tab"
        assert "创建时状态 QUEUED" in app.last_notice
        assert "最新执行状态" in app.last_notice
        assert any("task_chat" in line.text for line in app.query_one("#chat-log", RichLog).lines)
        assert app._usage == [DONE["usage"]]
        service.task_state = "WAITING_APPROVAL"
        await app._refresh()
        assert "WAITING_APPROVAL" in str(app.query_one("#sidebar-body", Static).render())
        service.task_state = "SUCCEEDED"
        await app._refresh()
        assert "SUCCEEDED" in str(app.query_one("#sidebar-body", Static).render())
        assert "尚未完成" not in app.last_notice


@pytest.mark.asyncio
async def test_context_sources_and_warning_survive_render_but_do_not_become_model_history():
    pytest.importorskip("textual")
    from test_tui_app import FakeService, settled
    from textual.widgets import Input, RichLog

    from personal_assistant.cli.tui.app import WorkbenchApp
    class Service(FakeService):
        async def status(self):
            return {**await super().status(), "model_disclosure_mode": "automatic"}
        async def conversation_stream(self, message, history, key, *, on_delta=None):
            return {**DONE, "sources": [{"source_uri": "file:///test/note.md",
                    "locator": {"line_start": 3, "line_end": 5}}],
                    "warnings": ["另一个资料提供方暂不可用"]}
    app = WorkbenchApp(Service(), refresh_seconds=60)
    async with app.run_test(size=(120, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "检索资料"
        await pilot.click("#ask")
        await settled(app)
        app._render_chat()
        await pilot.pause()
        rendered = "\n".join(line.text for line in app.query_one("#chat-log", RichLog).lines)
        assert "file:///test/note.md" in rendered and "line_start" in rendered
        assert "另一个资料提供方暂不可用" in rendered
        assert app.history[-1]["content"] == DONE["answer"]
        await pilot.click("#clear-chat")
        await settled(app)
        app._render_chat()
        assert not app._chat_receipts


@pytest.mark.asyncio
async def test_task_read_failure_does_not_turn_confirmed_creation_into_failed_chat():
    pytest.importorskip("textual")
    from test_tui_app import FakeService, settled
    from textual.widgets import Input

    from personal_assistant.cli.tui.app import WorkbenchApp
    class Service(FakeService):
        async def status(self):
            return {**await super().status(), "model_disclosure_mode": "automatic"}
        async def conversation_stream(self, message, history, key, *, on_delta=None):
            return {**DONE, "task": {"id": "task_chat", "state": "QUEUED"}}
        async def task(self, task_id):
            raise WorkbenchError("CONNECTION_UNAVAILABLE")
    app = WorkbenchApp(Service(), refresh_seconds=60)
    async with app.run_test(size=(120, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "建立计划"
        await pilot.click("#ask")
        await settled(app)
        assert len(app.history) == 2 and app.selected_task_id == "task_chat"
        assert "已创建" in app.last_notice and "task_chat" in app.last_notice
        assert not app.service.pending
