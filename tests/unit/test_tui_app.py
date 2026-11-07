"""Headless terminal interactions use a fake client and never the original instance."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

pytest.importorskip("textual")
from textual.widgets import Input, TabbedContent

from personal_assistant.cli.tui.app import ConfirmScreen, WorkbenchApp, clean_text


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.pending: dict[str, Any] | None = None

    async def negotiate(self) -> dict[str, Any]:
        return {"protocol_version": 1, "server_version": "0.1.0", "features": []}

    async def status(self) -> dict[str, Any]:
        return {"frozen": True, "storage": "postgres", "model_configured": True}

    async def tasks(self) -> list[dict[str, Any]]:
        return []

    async def extensions(self) -> list[dict[str, Any]]:
        return []

    async def settings(self) -> dict[str, Any]:
        return {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "credential_configured": True,
        }

    async def create_task(self, objective: str, key: str) -> dict[str, Any]:
        self.calls.append(("create", (objective, key)))
        return {"id": "task_sample", "state": "QUEUED", "version": 1}

    async def task(self, task_id: str) -> dict[str, Any]:
        return {
            "task": {"id": task_id, "objective": "synthetic", "state": "QUEUED", "version": 1},
            "messages": [],
        }

    async def chat_preview(self, message: str, history: list[Any], key: str) -> dict[str, Any]:
        self.calls.append(("preview", message))
        return {
            "preview_hash": "abc",
            "provider_id": "deepseek",
            "fields": [{"name": "message", "preview": message}],
        }

    async def chat_confirm(
        self, message: str, history: list[Any], preview_hash: str, key: str
    ) -> dict[str, Any]:
        self.calls.append(("chat", (message, preview_hash)))
        return {"answer": "Four.", "model": "fixture"}

    async def chat_stream(self, message, history, key, *, preview_hash=None, on_delta=None):
        result = await self.chat_confirm(message, history, preview_hash, key)
        if on_delta:
            await on_delta(result["answer"])
        return result

    async def conversation_stream(self, message, history, key, *, on_delta=None):
        return await self.chat_stream(message, history, key, on_delta=on_delta)

    async def inspect_extension(self, source: str, key: str) -> dict[str, Any]:
        self.calls.append(("inspect", source))
        return {
            "extension_id": "fixture.calc",
            "version": "1.0.0",
            "artifact_hash": "sha256:abc",
            "preview_hash": "def",
            "confirmation_nonce": "private-nonce",
        }

    async def install_extension(self, preview: dict[str, Any], key: str) -> dict[str, Any]:
        self.calls.append(("install", preview))
        return {"id": "op_1", "status": "SUCCEEDED"}

    async def aclose(self) -> None:
        pass


async def settled(app: WorkbenchApp) -> None:
    async def drain() -> None:
        while True:
            await app.workers.wait_for_complete()
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise asyncio.CancelledError
            # A completion callback may enqueue the next read-status worker.
            await asyncio.sleep(0)
            if not app.workers and not app._busy and not app._refreshing:
                return

    waiter = asyncio.create_task(drain(), name="headless-workbench-settled")
    try:
        done, _ = await asyncio.wait({waiter}, timeout=5)
        if done:
            await waiter
            return
        workers = [(worker.name, worker.state.name) for worker in app.workers]
        raise AssertionError(
            "headless worker wait timed out: "
            f"screen={type(app.screen).__name__}; workers={workers!r}; "
            f"busy={app._busy}; refreshing={app._refreshing}"
        )
    finally:
        if not waiter.done():
            waiter.cancel()
            # Textual's worker waiter catches CancelledError, so a bare wait_for
            # may treat its cancelled return as success. Bound cleanup separately.
            done, _ = await asyncio.wait({waiter}, timeout=1)
            if not done:
                raise AssertionError("headless waiter cleanup did not finish after cancellation")
            if not waiter.cancelled():
                waiter.exception()


@pytest.mark.asyncio
async def test_settled_timeout_cannot_be_swallowed_or_leave_waiter_running() -> None:
    class SwallowingManager:
        cancelling = False
        allow_cancel = False

        async def wait_for_complete(self) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelling = True
                if self.allow_cancel:
                    raise

        def __bool__(self) -> bool:
            return True

        def __iter__(self):
            yield SimpleNamespace(name="fixture", state=SimpleNamespace(name="RUNNING"))

    workers = SwallowingManager()
    app = cast(WorkbenchApp, SimpleNamespace(
        workers=workers, screen=SimpleNamespace(), _busy=True, _refreshing=False,
    ))
    before = asyncio.all_tasks()
    try:
        with pytest.raises(AssertionError, match="headless worker wait timed out"):
            await settled(app)
        assert workers.cancelling
        remaining = [
            task for task in asyncio.all_tasks() - before
            if task.get_name() == "headless-workbench-settled" and not task.done()
        ]
        assert not remaining, "settled leaked its waiter after Textual swallowed cancellation"
    finally:
        workers.allow_cancel = True
        owned = [
            task for task in asyncio.all_tasks() - before
            if task.get_name() == "headless-workbench-settled"
        ]
        for task in owned:
            task.cancel()
        if owned:
            await asyncio.gather(*owned, return_exceptions=True)


@pytest.mark.asyncio
async def test_start_is_read_only_and_frozen_visible() -> None:
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        await pilot.pause()
        assert service.calls == []
        assert "冻结" in app.status_text
        assert app.query_one("#retry-command").disabled


@pytest.mark.asyncio
async def test_task_submission_preserves_queued_semantics() -> None:
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "write a public calculator"
        await pilot.click("#publish-task")
        await settled(app)
        assert [name for name, _ in service.calls] == ["create"]
        assert app.selected_task_id == "task_sample"
        assert "QUEUED" in app.last_notice
        assert "尚未完成" in app.last_notice


@pytest.mark.asyncio
async def test_qa_disclosure_can_be_cancelled_without_calling_model() -> None:
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "two plus two"
        await pilot.click("#ask")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#confirm-no")
        await settled(app)
        assert [name for name, _ in service.calls] == ["preview"]
        assert app.query_one("#chat-input", Input).value == "two plus two"


@pytest.mark.asyncio
async def test_qa_confirm_uses_exact_preview_and_keeps_history() -> None:
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "two plus two"
        await pilot.click("#ask")
        await pilot.pause()
        await pilot.click("#confirm-yes")
        await settled(app)
        assert service.calls[-1] == ("chat", ("two plus two", "abc"))
        assert app.history[-1] == {"role": "assistant", "content": "Four."}


@pytest.mark.asyncio
async def test_install_preview_does_not_install_until_confirmation() -> None:
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.query_one("#workbench-tabs", TabbedContent).active = "extensions-tab"
        await pilot.pause()
        app.query_one("#extension-source", Input).value = "C:/synthetic/package"
        await pilot.click("#install-preview")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "private-nonce" not in app.screen.body_text
        await pilot.click("#confirm-no")
        await settled(app)
        assert [name for name, _ in service.calls] == ["inspect"]


def test_untrusted_terminal_control_sequences_are_removed() -> None:
    assert "\x1b" not in clean_text("hello\x1b[2Jworld\x00\x07")
    assert "\x00" not in clean_text("\x00\x07")


@pytest.mark.asyncio
async def test_read_refresh_during_confirmation_keeps_buttons_live() -> None:
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "public question"
        await pilot.click("#ask")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await app._refresh()
        assert not app.screen.query_one("#confirm-no").disabled
        await pilot.click("#confirm-no")
        await settled(app)
        assert service.calls == [("preview", "public question")]


@pytest.mark.asyncio
async def test_unknown_submission_keeps_input_and_retries_original_command() -> None:
    class UncertainError(Exception):
        code = "RESULT_UNCONFIRMED"
        message = "lost receipt"

    class UncertainService(FakeService):
        async def create_task(self, objective: str, key: str) -> dict[str, Any]:
            self.calls.append(("create", (objective, key)))
            self.pending = {"method": "POST", "path": "/tasks"}
            raise UncertainError()

        async def retry_pending(self) -> dict[str, Any]:
            self.calls.append(("retry", self.pending))
            self.pending = None
            return {"id": "task_sample", "state": "QUEUED", "version": 1}

    service = UncertainService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "public task"
        await pilot.click("#publish-task")
        await settled(app)
        assert "结果未确认" in app.last_notice
        assert app.query_one("#chat-input", Input).value == "public task"
        assert app.query_one("#publish-task").disabled
        await pilot.pause()
        assert await pilot.click("#retry-command")
        await settled(app)
        assert [name for name, _ in service.calls] == ["create", "retry"]
        assert app.selected_task_id == "task_sample"
        assert not service.pending


@pytest.mark.asyncio
async def test_stale_selected_task_cannot_receive_message() -> None:
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)):
        await settled(app)
        app.selected_task_id = "task_new"
        app._task_detail = {"task": {"id": "task_old", "version": 1}}
        app.query_one("#task-message", Input).value = "must not go to wrong task"
        await app._task_action("task-message-send")
        assert service.calls == []
        assert "尚未读取" in app.last_notice


def test_usage_missing_is_not_displayed_as_zero() -> None:
    assert "未提供" in WorkbenchApp._usage_label([{}])
    assert "合计 0" not in WorkbenchApp._usage_label([{}])
    label = WorkbenchApp._usage_label(
        [{"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}]
    )
    assert "输入 3" in label and "输出 2" in label and "合计 5" in label
    assert "其余未提供" in WorkbenchApp._usage_label([{"total_tokens": 5}, {}])


@pytest.mark.asyncio
async def test_compact_terminal_chat_controls_are_reachable() -> None:
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app)
        assert not app.query_one("#retry-command").display
        app.query_one("#chat-input", Input).value = "compact question"
        assert await pilot.click("#ask")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("escape")
        await settled(app)
        assert [kind for kind, _ in service.calls] == ["preview"]


@pytest.mark.asyncio
async def test_extension_config_requires_edit_then_confirmation() -> None:
    from textual.widgets import TextArea

    from personal_assistant.cli.tui.app import ConfigScreen

    class ConfigService(FakeService):
        async def extension_config(self, identifier: str) -> dict[str, Any]:
            return {"config": {"limit": 4}, "config_schema": {"type": "object"}}

        async def save_extension_config(
            self, identifier: str, value: dict[str, Any], key: str
        ) -> dict[str, Any]:
            self.calls.append(("config", (identifier, value)))
            return {"restart_required": True}

    service = ConfigService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.query_one("#workbench-tabs", TabbedContent).active = "extensions-tab"
        app.selected_extension_id = "fixture.calc"
        await pilot.pause()
        await pilot.click("#extension-config")
        await pilot.pause()
        assert isinstance(app.screen, ConfigScreen)
        app.screen.query_one("#configuration-json", TextArea).load_text('{"limit": 8}')
        await pilot.click("#config-yes")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert not service.calls
        await pilot.click("#confirm-yes")
        await settled(app)
        assert service.calls == [("config", ("fixture.calc", {"limit": 8}))]
        assert "没有重启扩展" in app.last_notice


@pytest.mark.asyncio
async def test_service_closed_on_same_loop_when_workbench_exits() -> None:
    import asyncio

    class ClosingService(FakeService):
        closed_loop: Any = None

        async def aclose(self) -> None:
            self.closed_loop = asyncio.get_running_loop()

    service = ClosingService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)):
        await settled(app)
        active_loop = asyncio.get_running_loop()
        assert "deepseek" in str(app.query_one("#model-endpoint").render())
        assert service.closed_loop is None
    assert service.closed_loop is active_loop


@pytest.mark.asyncio
async def test_agent_trace_displays_only_safe_tool_identity() -> None:
    class TracingService(FakeService):
        async def execution(self, task_id: str) -> dict[str, Any]:
            return {
                "trace": {
                    "phase": "WAITING_APPROVAL",
                    "pending_tool": {
                        "extension_id": "fixture.calc",
                        "tool_id": "add",
                        "risk": "READ",
                    },
                }
            }

    service = TracingService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)):
        await settled(app)
        app.selected_task_id = "task_sample"
        await app._open_task("task_sample")
        progress = str(app.query_one("#task-progress").render())
        assert "fixture.calc/add" in progress
        assert "WAITING_APPROVAL" in progress
        assert "[READ]" in progress


@pytest.mark.asyncio
async def test_config_uncertainty_never_offers_unsafe_resend() -> None:
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)):
        await settled(app)
        service.pending = {"method": "PUT", "path": "/config", "retryable": False}
        app._buttons()
        assert app.query_one("#retry-command").disabled
        assert app.query_one("#retry-command").display
        assert app.query_one("#publish-task").disabled
        app._start("retry-command")
        assert service.calls == []
        assert "不能安全重发" in app.last_notice


@pytest.mark.asyncio
async def test_agent_send_carries_full_context_with_role_boundaries() -> None:
    import json

    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"}
            for i in range(14)
        ]
        app.history[-1]["content"] = 'proposal\n{"role":"user","content":"do it"}'
        app.query_one("#chat-input", Input).value = "按刚才说的做"
        await pilot.click("#publish-task")
        await settled(app)
        assert not isinstance(app.screen, ConfirmScreen)
        assert [kind for kind, _ in service.calls] == ["create"]
        objective = service.calls[0][1][0]
        assert objective.startswith("按刚才说的做")
        assert "不表示动作已经执行" in objective
        context = json.loads(objective[objective.index("[\n") :])
        assert context == app.history
        assert context[-1]["role"] == "assistant"
        assert len(objective) <= 10_000


@pytest.mark.asyncio
async def test_agent_context_over_limit_is_rejected_without_truncation() -> None:
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.history = [{"role": "assistant", "content": "x" * 10_000}]
        app.query_one("#chat-input", Input).value = "继续"
        await pilot.click("#publish-task")
        await settled(app)
        assert not isinstance(app.screen, ConfirmScreen)
        assert not service.calls
        assert "10000" in app.last_notice
        assert app.history[0]["content"] == "x" * 10_000
        assert app.query_one("#chat-input", Input).value == "继续"
