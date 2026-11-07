"""Task/extension pane sizing and refresh stability, without live services."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")
from test_tui_app import FakeService, settled
from textual.widgets import DataTable

from personal_assistant.cli.tui.app import WorkbenchApp


class Lists(FakeService):
    changed = False
    prepend = False

    async def tasks(self):
        rows = [
            {
                "id": f"task_{n}",
                "objective": "较长的任务名称，阅读文档并检查进度 " * 5,
                "state": "FAILED" if self.changed and n == 25 else "WAITING_RECONCILIATION",
                "version": 2 if self.changed and n == 25 else 1,
            }
            for n in range(40)
        ]
        if self.prepend:
            rows.insert(0, {"id": "new", "objective": "new task", "state": "QUEUED", "version": 1})
        return rows

    async def extensions(self):
        return [
            {"id": f"fixture.long_extension_name_{n}", "version": "0.1.0", "state": "DISCOVERED"}
            for n in range(40)
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tab,selector", [("tasks-tab", "#tasks-table"), ("extensions-tab", "#extensions-table")]
)
async def test_lists_use_available_pane_and_fit_columns(tab, selector):
    app = WorkbenchApp(Lists(), refresh_seconds=60)
    async with app.run_test(size=(120, 40)) as pilot:
        await settled(app)
        app.action_tab(tab)
        await pilot.pause()
        table = app.query_one(selector, DataTable)
        assert table.size.height >= 13
        assert table.max_scroll_x == 0
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert table.size.height >= 7
        assert table.max_scroll_x == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tab,selector", [("tasks-tab", "#tasks-table"), ("extensions-tab", "#extensions-table")]
)
async def test_polling_does_not_rebuild_or_pull_scrolled_list_back_to_cursor(
    tab, selector, monkeypatch
):
    app = WorkbenchApp(Lists(), refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        app.action_tab(tab)
        await pilot.pause()
        table = app.query_one(selector, DataTable)
        table.move_cursor(row=0, animate=False)
        table.scroll_to(y=20, animate=False, immediate=True, force=True)
        await pilot.pause()
        initial = table.scroll_y
        assert initial > 0
        cleared = []
        original = table.clear

        def clear(*args, **kwargs):
            cleared.append(True)
            return original(*args, **kwargs)

        monkeypatch.setattr(table, "clear", clear)
        await app._refresh()
        await pilot.pause()
        assert table.scroll_y == initial
        assert not cleared
        app.service.changed = True
        await app._refresh()
        await pilot.pause()
        assert table.scroll_y == initial
        assert not cleared


@pytest.mark.asyncio
async def test_insert_preserves_visible_row_and_selection():
    app = WorkbenchApp(Lists(), refresh_seconds=60)
    async with app.run_test(size=(110, 36)) as pilot:
        await settled(app)
        app.action_tab("tasks-tab")
        await pilot.pause()
        table = app.query_one("#tasks-table", DataTable)
        table.move_cursor(row=25, animate=False)
        await pilot.pause()
        table.scroll_to(y=20, animate=False, immediate=True, force=True)
        await pilot.pause()
        app.service.prepend = True
        await app._refresh()
        await pilot.pause()
        assert list(table.rows)[table.cursor_row].value == "task_25"
        assert table.scroll_y == 21


class Managed(Lists):
    async def tasks(self):
        return [
            {
                "id": "task_one",
                "objective": "one",
                "state": "QUEUED",
                "version": 4,
                "queue_job_id": "job_one",
                "queue_version": 7,
                "queue_position": 1,
            }
        ]

    async def task(self, identifier):
        return {
            "task": {"id": identifier, "objective": "one", "state": "QUEUED", "version": 4},
            "messages": [],
        }

    async def move_task(self, task_id, job_id, version, direction, key):
        self.calls.append(("move", (task_id, job_id, version, direction, key)))
        return {}

    async def remove_task(self, task_id, version, key):
        self.calls.append(("remove", (task_id, version, key)))
        return {}


@pytest.mark.asyncio
async def test_queue_controls_move_with_observed_job_and_remove_needs_confirmation():
    from personal_assistant.cli.tui.app import ConfirmScreen

    service = Managed()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app)
        app.action_tab("tasks-tab")
        app.selected_task_id = "task_one"
        await app._open_task("task_one")
        await pilot.pause()
        assert await pilot.click("#task-move-up")
        await settled(app)
        assert service.calls[0][0] == "move"
        assert service.calls[0][1][:4] == ("task_one", "job_one", 7, "up")
        assert await pilot.click("#task-remove")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert len(service.calls) == 1
        await pilot.click("#confirm-no")
        await settled(app)
        assert len(service.calls) == 1
        await pilot.click("#task-remove")
        await pilot.pause()
        await pilot.click("#confirm-yes")
        await settled(app)
        assert service.calls[-1][0] == "remove"
        assert service.calls[-1][1][:2] == ("task_one", 4)
        assert app.selected_task_id is None


@pytest.mark.asyncio
async def test_waiting_task_explains_nonrunning_and_disables_sort():
    from textual.widgets import Button, Static

    class Waiting(Managed):
        async def tasks(self):
            return [{"id": "task_one", "objective": "one", "state": "WAITING_USER", "version": 4}]

        async def task(self, identifier):
            return {
                "task": {
                    "id": identifier,
                    "objective": "one",
                    "state": "WAITING_USER",
                    "version": 4,
                },
                "messages": [],
            }

    app = WorkbenchApp(Waiting(), refresh_seconds=60)
    async with app.run_test(size=(100, 36)):
        await settled(app)
        app.selected_task_id = "task_one"
        await app._open_task("task_one")
        for selector in ("#task-move-up", "#task-move-down", "#task-move-top"):
            assert app.query_one(selector, Button).disabled
        assert "等待补充" in str(app.query_one("#task-progress", Static).render())
        assert "不占用" in str(app.query_one("#task-progress", Static).render())


@pytest.mark.asyncio
async def test_unresolved_external_task_is_visible_and_cannot_be_removed_or_moved():
    from textual.widgets import Button, Static

    class Unresolved(Managed):
        async def tasks(self):
            return [
                {
                    **(await super().tasks())[0],
                    "state": "CANCELLED",
                    "requires_reconciliation": True,
                }
            ]

    app = WorkbenchApp(Unresolved(), refresh_seconds=60)
    async with app.run_test(size=(100, 36)):
        await settled(app)
        app.selected_task_id = "task_one"
        await app._open_task("task_one")
        assert app.query_one("#task-remove", Button).disabled
        assert app.query_one("#task-move-up", Button).disabled
        assert "待对账" in str(app.query_one("#task-progress", Static).render())


@pytest.mark.asyncio
async def test_late_unknown_after_removal_does_not_report_hidden_task():
    class Late(Managed):
        unresolved = False

        async def tasks(self):
            return [{**(await super().tasks())[0], "requires_reconciliation": self.unresolved}]

        async def remove_task(self, *args):
            self.unresolved = True
            return {}

    app = WorkbenchApp(Late(), refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.action_tab("tasks-tab")
        app.selected_task_id = "task_one"
        await app._open_task("task_one")
        await pilot.pause()
        await pilot.click("#task-remove")
        await pilot.pause()
        await pilot.click("#confirm-yes")
        await settled(app)
        assert "待核对" in app.last_notice
        assert app.selected_task_id == "task_one"


@pytest.mark.asyncio
async def test_arrow_highlight_cannot_move_or_remove_previous_selected_task():
    from textual.widgets import Button

    class Pair(Managed):
        async def tasks(self):
            first = (await super().tasks())[0]
            return [
                first,
                {**first, "id": "task_two", "queue_job_id": "job_two", "queue_version": 9},
            ]

    service = Pair()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(100, 36)) as pilot:
        await settled(app)
        app.action_tab("tasks-tab")
        table = app.query_one("#tasks-table", DataTable)
        table.focus()
        await pilot.pause()
        await pilot.press("enter")
        await settled(app)
        assert app.selected_task_id == "task_one"
        await pilot.press("down")
        await pilot.pause()
        assert app.selected_task_id == "task_one"
        for selector in ("#task-move-up", "#task-move-down", "#task-move-top", "#task-remove"):
            assert app.query_one(selector, Button).disabled
        await app._task_action("task-move-up")
        await app._task_action("task-remove")
        assert not service.calls
        assert "Enter" in app.last_notice
        await pilot.press("enter")
        await settled(app)
        assert app.selected_task_id == "task_two"
        assert not app.query_one("#task-move-up", Button).disabled
        await pilot.click("#task-move-up")
        await settled(app)
        assert service.calls[0][1][:4] == ("task_two", "job_two", 9, "up")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tab,selector",
    [("tasks-tab", "#task-actions Button"), ("extensions-tab", "#extensions-tab .buttons Button")],
)
async def test_actions_remain_inside_viewport_after_terminal_resize(tab, selector):
    app = WorkbenchApp(Lists(), refresh_seconds=60)
    async with app.run_test(size=(120, 40)) as pilot:
        await settled(app)
        app.action_tab(tab)
        for width, height in ((120, 40), (80, 24), (120, 40)):
            await pilot.resize_terminal(width, height)
            await pilot.pause()
            for button in app.query(selector):
                assert button.region.x >= 0, button.id
                assert button.region.right <= width, (button.id, button.region)
                assert button.region.bottom <= height - 1, (button.id, button.region)
