"""Reconciliation is explicit and never silently resumes an external operation."""
import json

import httpx
import pytest
from textual.widgets import Button

from personal_assistant.cli.tui.app import WorkbenchApp
from personal_assistant.cli.tui.service import WorkbenchError, WorkbenchService
from tests.unit.test_tui_app import FakeService, settled


async def test_reconciliation_timeout_retries_exact_original_command():
    calls = []
    async def respond(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("receipt lost", request=request)
        return httpx.Response(200, json={"resolved": True, "state": "WAITING_USER"})
    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkbenchError) as error:
            await service.reconcile_task("task_fixture", 3, "original")
        assert error.value.uncertain
        assert service.pending is not None
        with pytest.raises(WorkbenchError):
            await service.resume_task("task_fixture", 3, "new-key")
        assert len(calls) == 1
        result = await service.retry_pending()
        assert result["resolved"]
        assert calls[0].url.path == "/admin/v1/workbench/tasks/task_fixture/reconcile"
        assert calls[0].content == calls[1].content
        assert json.loads(calls[0].content) == {"version": 3}
        assert calls[1].headers["Idempotency-Key"] == "original"
    finally:
        await service.aclose()


class Service(FakeService):
    supported = True
    resolved = True
    def __init__(self):
        super().__init__()
        self.state = "WAITING_RECONCILIATION"
        self.version = 3
    async def negotiate(self):
        return {"protocol_version": 1, "server_version": "fixture",
                "features": ["task_reconciliation"] if self.supported else []}
    async def tasks(self):
        return [{"id": "task_fixture", "objective": "public fixture", "state": self.state,
                 "version": self.version,
                 "requires_reconciliation": self.state == "WAITING_RECONCILIATION"}]
    async def task(self, task_id):
        return {"task": (await self.tasks())[0], "messages": []}
    async def reconcile_task(self, task_id, version, key):
        self.calls.append(("reconcile", (task_id, version, key)))
        if self.resolved:
            self.state = "WAITING_USER"
            self.version += 1
        return {"task_id": task_id, "resolved": self.resolved, "state": self.state}
    async def resume_task(self, *args, **kwargs):
        self.calls.append(("resume", args))
        return {}


@pytest.mark.parametrize("resolved", [False, True])
async def test_tui_reconciliation_keeps_explicit_resume_separate(resolved):
    service = Service()
    service.resolved = resolved
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(150, 45)):
        await settled(app)
        app.selected_task_id = "task_fixture"
        await app._open_task("task_fixture")
        app._buttons()
        assert not app.query_one("#task-reconcile", Button).disabled
        assert app.query_one("#task-resume", Button).disabled
        app._start("task-reconcile")
        await settled(app)
        assert [name for name, _ in service.calls] == ["reconcile"]
        assert service.calls[0][1][:2] == ("task_fixture", 3)
        assert ("继续处理" if resolved else "未决") in app.last_notice
        assert app.query_one("#task-resume", Button).disabled is not resolved


async def test_old_backend_disables_only_reconciliation_and_no_post():
    service = Service()
    service.supported = False
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(150, 45)):
        await settled(app)
        app.selected_task_id = "task_fixture"
        await app._open_task("task_fixture")
        app._buttons()
        assert app.query_one("#task-reconcile", Button).disabled
        app._start("task-reconcile")
        await settled(app)
        assert service.calls == []
        assert "重启" in app.last_notice
