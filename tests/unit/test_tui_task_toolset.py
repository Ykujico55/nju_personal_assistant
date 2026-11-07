"""Task recreation copies only the goal and never resumes unknown external work."""

import asyncio

import pytest

from personal_assistant.cli.tui.app import ConfirmScreen, WorkbenchApp
from tests.unit.test_tui_app import FakeService, settled


class Service(FakeService):
    recreate = True

    async def task(self, task_id):
        return {
            "task": {
                "id": task_id,
                "state": "WAITING_USER",
                "version": 3,
                "objective": "original public goal",
            },
            "messages": [],
        }

    async def execution(self, task_id):
        return {
            "trace": {
                "toolset": {
                    "state": "CHANGED",
                    "can_recreate": self.recreate,
                    "message": "本任务固定旧工具；新增工具需要新建任务。",
                    "added": ["fixture.new"],
                }
            }
        }


async def modal_state(app, *, open):
    async def observed():
        while isinstance(app.screen, ConfirmScreen) != open:
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(observed(), timeout=5)
    except TimeoutError:
        raise AssertionError(
            f"confirmation state did not become open={open}; "
            f"screen={type(app.screen).__name__}; busy={app._busy}"
        ) from None


@pytest.mark.asyncio
async def test_toolset_hint_and_recreate_copies_only_goal():
    service = Service()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(120, 45)) as pilot:
        await settled(app)
        app.selected_task_id = "task_old"
        await app._open_task("task_old")
        assert "固定旧工具" in str(app.query_one("#task-progress").render())
        app._start("task-recreate")
        await modal_state(app, open=True)
        await asyncio.wait_for(pilot.pause(), timeout=5)
        assert isinstance(app.screen, ConfirmScreen)
        assert await asyncio.wait_for(pilot.click("#confirm-yes"), timeout=5)
        await modal_state(app, open=False)
        await settled(app)
        assert [name for name, _ in service.calls] == ["create"]
        assert service.calls[0][1][0] == "original public goal"
        assert app.selected_task_id == "task_sample"


@pytest.mark.asyncio
async def test_unknown_or_running_task_does_not_offer_recreation():
    service = Service()
    service.recreate = False
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(120, 45)):
        await settled(app)
        app.selected_task_id = "task_old"
        await app._open_task("task_old")
        app._start("task-recreate")
        await settled(app)
        assert service.calls == []
        assert "不能" in app.last_notice
        assert not isinstance(app.screen, ConfirmScreen)


@pytest.mark.asyncio
async def test_recreate_rechecks_after_confirmation():
    service = Service()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(120, 45)) as pilot:
        await settled(app)
        app.selected_task_id = "task_old"
        await app._open_task("task_old")
        app._start("task-recreate")
        await modal_state(app, open=True)
        await asyncio.wait_for(pilot.pause(), timeout=5)
        assert isinstance(app.screen, ConfirmScreen)
        service.recreate = False
        assert await asyncio.wait_for(pilot.click("#confirm-yes"), timeout=5)
        await modal_state(app, open=False)
        await settled(app)
        assert service.calls == []
        assert "原任务状态已变化" in app.last_notice
