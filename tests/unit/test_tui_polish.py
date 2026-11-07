"""Small terminal UX checks; no host connection or model invocation."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")
from test_tui_app import FakeService, settled
from textual.widgets import Input, TabbedContent, TextArea

from personal_assistant.cli.tui.app import WorkbenchApp


@pytest.mark.asyncio
async def test_slash_navigation_does_not_call_model():
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "/extensions"
        app.query_one("#chat-input", Input).focus()
        await pilot.press("enter")
        assert app.query_one("#workbench-tabs", TabbedContent).active == "extensions-tab"
        assert not service.calls


@pytest.mark.asyncio
async def test_unknown_slash_keeps_input_and_never_calls_provider():
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app)
        app.query_one("#chat-input", Input).value = "/shell rm anything"
        app.query_one("#chat-input", Input).focus()
        await pilot.press("enter")
        assert not service.calls
        assert "未知命令" in app.last_notice
        assert app.query_one("#chat-input", Input).value == "/shell rm anything"


@pytest.mark.asyncio
async def test_agent_mode_enter_uses_real_task_path_not_qa():
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        field = app.query_one("#chat-input", Input)
        field.focus()
        field.value = "/agent"
        await pilot.press("enter")
        field.value = "public fixture"
        await pilot.press("enter")
        await settled(app)
        assert [name for name, _ in service.calls] == ["create"]


@pytest.mark.asyncio
async def test_multiline_editor_preserves_text_without_sending():
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        await pilot.press("ctrl+o")
        await pilot.pause()
        app.screen.query_one("#message-editor", TextArea).load_text("line one\nline two")
        await pilot.click("#editor-apply")
        await pilot.pause()
        assert app.composer_value() == "line one" + chr(10) + "line two"
        assert app.query_one("#chat-input", Input).value == "line one ↵ line two"
        app._set_composer(r"C:\new\notes.txt")
        assert app.query_one("#chat-input", Input).value == r"C:\new\notes.txt"
        assert app.composer_value() == r"C:\new\notes.txt"
        assert not service.calls


@pytest.mark.asyncio
async def test_f1_help_and_responsive_sidebar():
    service = FakeService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        assert app.query_one("#context-sidebar").display
        sidebar = str(app.query_one("#sidebar-body").render())
        assert chr(10) in sidebar
        assert chr(92) + "n" not in sidebar
        await pilot.press("f1")
        await pilot.pause()
        assert app.screen.query_one("#command-help")
        await pilot.press("escape")
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert not app.query_one("#context-sidebar").display
        assert await pilot.click("#ask")


def test_markdown_cannot_emit_terminal_controls_or_clickable_links():
    from io import StringIO

    from rich.console import Console

    from personal_assistant.cli.tui.panels import safe_markdown

    output = StringIO()
    console = Console(file=output, force_terminal=False, width=70)
    markdown = safe_markdown("**bold** [link](https://example.com)\x1b[2J\x00")
    console.print(markdown)
    assert not markdown.hyperlinks
    assert "\x1b" not in output.getvalue() and "\x00" not in output.getvalue()


@pytest.mark.asyncio
async def test_refresh_keeps_selected_table_row_and_history_position():
    from textual.widgets import DataTable, RichLog

    class Populated(FakeService):
        added = False

        async def tasks(self):
            return [
                {"id": f"task_{n}", "objective": f"task {n}", "state": "QUEUED", "version": 1}
                for n in range(20)
            ]

        async def task(self, task_id):
            return {
                "task": {"id": task_id, "objective": "fixture", "state": "RUNNING", "version": 1},
                "messages": [{"role": "assistant", "content": "historical line\n" * 80}],
            }

    app = WorkbenchApp(Populated(), refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        app.action_tab("tasks-tab")
        await pilot.pause()
        table = app.query_one("#tasks-table", DataTable)
        table.move_cursor(row=10)
        app.selected_task_id = "task_10"
        await app._open_task("task_10")
        await pilot.pause()
        log = app.query_one("#task-detail", RichLog)
        log.scroll_to(y=0, animate=False, immediate=True, force=True)
        await pilot.pause()
        await app._refresh()
        await pilot.pause()
        assert table.cursor_row == 10
        assert log.scroll_y == 0
        app.service.added = True
        await app._open_task("task_10")
        await pilot.pause()
        assert log.scroll_y == 0


@pytest.mark.asyncio
async def test_absent_local_settings_does_not_hide_usage_operation_or_worker_status():
    class MissingSettings(FakeService):
        async def settings(self):
            raise ValueError("MODEL_CONFIG_UNAVAILABLE")

        async def status(self):
            return {"frozen": False, "worker_online": False, "storage": "postgres"}

        async def usage(self):
            return {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}

        async def operation(self, identifier):
            return {"status": "SUCCEEDED"}

    app = WorkbenchApp(MissingSettings(), refresh_seconds=60)
    app._operation_id = "fixture-op"
    async with app.run_test(size=(110, 36)):
        await settled(app)
        assert "Worker 未连接" in app.status_text
        assert "服务不可用" not in app.status_text
        assert "合计 7" in str(app.query_one("#instance-usage").render())
        assert app._operation_id is None


@pytest.mark.asyncio
async def test_explicit_settings_reload_confirms_dirty_fields_and_loads_new_revision():
    class ChangingSettings(FakeService):
        revision = "one"

        async def settings(self):
            return {**await super().settings(), "revision": self.revision}

    service = ChangingSettings()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        app.action_tab("settings-tab")
        await pilot.pause()
        app.query_one("#model-name", Input).value = "unsaved"
        service.revision = "two"
        await app._refresh()
        assert app.query_one("#model-name", Input).value == "unsaved"
        await pilot.click("#reload-settings")
        await pilot.pause()
        from personal_assistant.cli.tui.app import ConfirmScreen

        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#confirm-no")
        await settled(app)
        assert app._settings["revision"] == "one"
        assert app.query_one("#model-name", Input).value == "unsaved"
        await pilot.click("#reload-settings")
        await pilot.pause()
        await pilot.click("#confirm-yes")
        await settled(app)
        assert app._settings["revision"] == "two"
        assert app.query_one("#model-name", Input).value == "deepseek-flash"


def test_markdown_headings_have_explicit_dark_background_contrast():
    from rich.console import Console
    from rich.theme import Theme

    from personal_assistant.cli.tui.panels import safe_markdown

    console = Console(
        color_system="truecolor",
        theme=Theme(
            {
                "markdown.h3": "bold black",
            }
        ),
    )
    segments = list(console.render(safe_markdown("### Heading contrast\n\nBody contrast")))
    heading = [segment for segment in segments if "Heading contrast" in segment.text]
    body = [segment for segment in segments if "Body contrast" in segment.text]
    assert heading and body
    assert heading[0].style.color.triplet.hex == "#c4a7ff"
    assert body[0].style.color.triplet.hex == "#d7d8e0"
    assert console.get_style("markdown.h3").color.name == "black"
