"""Offline TUI projections distinguish current queue from previous execution."""
from typing import Any

import pytest

pytest.importorskip("textual")
from personal_assistant.cli.tui.app import WorkbenchApp
from tests.unit.test_tui_app import FakeService, settled


@pytest.mark.asyncio
async def test_queued_task_labels_waiting_as_previous_execution():
    class Service(FakeService):
        async def execution(self, task_id: str) -> dict[str, Any]:
            return {"state": "QUEUED", "execution": {"state": "WAITING_USER"},
                    "trace": {"phase": "waiting", "pending_tool": None}}

    app = WorkbenchApp(Service(), refresh_seconds=60)
    async with app.run_test(size=(120, 40)):
        await settled(app)
        app.selected_task_id = "task_sample"
        await app._open_task("task_sample")
        progress = str(app.query_one("#task-progress").render())
        assert "等待领取" in progress
        assert "上次阶段" in progress


@pytest.mark.asyncio
async def test_safe_validation_code_visible_in_task_progress():
    class Service(FakeService):
        async def execution(self, task_id: str) -> dict[str, Any]:
            return {"trace": {"phase": "waiting", "pending_tool": None,
                "validation_failure": {"code": "PROPOSAL_INVALID_ARGUMENTS", "message": "fixed"}}}

    app = WorkbenchApp(Service(), refresh_seconds=60)
    async with app.run_test(size=(120, 40)):
        await settled(app)
        app.selected_task_id = "task_sample"
        await app._open_task("task_sample")
        progress = str(app.query_one("#task-progress").render())
        assert "PROPOSAL_INVALID_ARGUMENTS" in progress


@pytest.mark.asyncio
async def test_safe_dispatch_diagnostic_visible_without_stored_message():
    class Service(FakeService):
        async def execution(self, task_id):
            return {"trace": {"failure_diagnostic": {
                "code": "MODEL_CREDENTIAL_UNAVAILABLE", "correlation_id": "diag_" + "a" * 32,
                "message": "synthetic-private-secret must not be displayed",
            }}}
    app = WorkbenchApp(Service(), refresh_seconds=60)
    async with app.run_test(size=(120, 40)):
        await settled(app)
        app.selected_task_id = "task_sample"
        await app._open_task("task_sample")
        progress = str(app.query_one("#task-progress").render())
        assert "MODEL_CREDENTIAL_UNAVAILABLE" in progress
        assert "diag_" + "a" * 32 in progress
        assert "synthetic-private-secret" not in progress
