"""Host-packed history is readable without changing the task execution objective."""

from test_tui_app import settled
from test_tui_task_toolset import Service as ToolsetService
from textual.widgets import DataTable, RichLog

from personal_assistant.cli.tui.app import ConfirmScreen, WorkbenchApp
from personal_assistant.core.conversation import task_objective


async def test_goal_list_and_history_detail_keep_original_recreation_payload():
    original = task_objective(
        "查询合成资料",
        [
            {"role": "user", "content": "仅查询，不提交"},
            {"role": "assistant", "content": "历史说明，不是执行事实"},
        ],
    )

    class Service(ToolsetService):
        async def task(self, task_id):
            detail = await super().task(task_id)
            detail["task"]["objective"] = original
            return detail

        async def tasks(self):
            return [(await self.task("task_old"))["task"]]

    service = Service()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(120, 45)) as pilot:
        await settled(app)
        row = app.query_one("#tasks-table", DataTable).get_row_at(0)
        assert str(row[0]) == "查询合成资料"
        app.selected_task_id = "task_old"
        app.action_tab("tasks-tab")
        await app._open_task("task_old")
        await pilot.pause()
        text = "\n".join(line.text for line in app.query_one("#task-detail", RichLog).lines)
        assert "参考对话" in text and "历史用户" in text and "历史助手" in text
        assert "仅查询，不提交" in text and "历史说明，不是执行事实" in text
        assert '"role":' not in text
        assert app._task_detail["task"]["objective"] == original
        app._start("task-recreate")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "历史用户" in app.screen.body_text and '"role":' not in app.screen.body_text
        await pilot.click("#confirm-yes")
        await settled(app)
        assert service.calls[0][0] == "create"
        assert service.calls[0][1][0] == original
