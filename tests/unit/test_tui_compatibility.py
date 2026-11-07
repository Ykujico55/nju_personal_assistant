"""Capability preflight preserves local settings and never sends into an old backend."""

from __future__ import annotations

import pytest
from test_tui_app import FakeService, settled
from textual.widgets import Button, Input, Static

from personal_assistant.cli.tui.app import WorkbenchApp
from personal_assistant.cli.tui.service import WorkbenchError


class Service(FakeService):
    incompatible = False
    offline = False

    def __init__(self):
        super().__init__()
        self.remote_reads = 0
        self.negotiations = 0

    async def negotiate(self):
        self.negotiations += 1
        if self.incompatible:
            raise WorkbenchError(
                "WORKBENCH_INCOMPATIBLE", "工作台与后台接口不兼容，请重启更新后的后台。"
            )
        if self.offline:
            raise WorkbenchError("LOCAL_API_UNREACHABLE", "后台暂不可达，请检查服务是否启动。")
        return {"protocol_version": 1, "server_version": "0.1.0", "features": []}

    async def status(self):
        self.remote_reads += 1
        return await super().status()


@pytest.mark.parametrize("offline", [False, True])
async def test_local_settings_load_even_when_backend_cannot_be_used(offline):
    service = Service()
    service.incompatible, service.offline = not offline, offline
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)):
        await settled(app)
        assert service.negotiations == 1
        assert service.remote_reads == 0
        assert app.query_one("#model-name", Input).value == "deepseek-flash"
        assert "已配置" in str(app.query_one("#model-credential", Static).render())
        assert app.query_one("#ask", Button).disabled
        assert not app.query_one("#reload-settings", Button).disabled
        assert ("不可达" if offline else "重启") in app.status_text


async def test_incompatible_refresh_keeps_pending_key_input_and_confirmed_history():
    service = Service()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)):
        await settled(app)
        app._set_composer("保留这段输入")
        app.history.append({"role": "user", "content": "已确认的聊天"})
        pending = {"path": "/admin/v1/workbench/conversation/stream", "key": "original-key"}
        service.pending = pending
        service.incompatible = True
        await app._refresh()
        assert service.pending is pending and service.pending["key"] == "original-key"
        assert app.composer_value() == "保留这段输入"
        assert app.history == [{"role": "user", "content": "已确认的聊天"}]
        assert app.query_one("#retry-command", Button).disabled
        assert app.query_one("#discard-reply", Button).disabled
        assert "重启" in app.status_text
        assert service.calls == []


async def test_backend_changed_after_start_is_checked_before_send_and_recovers_on_refresh():
    service = Service()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)):
        await settled(app)
        app._set_composer("创建一个合成任务")
        service.incompatible = True
        await app._perform("publish-task")
        assert not service.calls
        assert app.composer_value() == "创建一个合成任务"
        assert "重启" in app.last_notice
        assert app.query_one("#publish-task", Button).disabled
        service.incompatible = False
        await app._refresh()
        assert not app.query_one("#publish-task", Button).disabled
        await app._perform("publish-task")
        assert [name for name, _ in service.calls] == ["create"]


@pytest.mark.parametrize(
    "action,expected_calls",
    [("install-preview", ["inspect"]), ("ask", ["preview"]), ("extension-enable", [])],
)
async def test_backend_changed_during_confirmation_blocks_final_request(
    action, expected_calls, monkeypatch
):
    class ConfirmService(Service):
        async def extension_action(self, identifier, action, key):
            self.calls.append(("extension-action", (identifier, action, key)))
            return {"id": "op_fixture", "status": "SUCCEEDED"}

    service = ConfirmService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 36)):
        await settled(app)
        app._set_composer("保留问题")
        app.history = [{"role": "user", "content": "已确认历史"}]
        app.query_one("#extension-source", Input).value = "synthetic/source"
        app.selected_extension_id = "fixture.calc"
        app._extensions = {"fixture.calc": {"state": "DISABLED"}}
        app._disclosure_mode = "manual"

        async def confirmed_after_backend_replaced(screen):
            service.incompatible = True
            return True

        monkeypatch.setattr(app, "push_screen_wait", confirmed_after_backend_replaced)
        await app._perform(action)
        assert [name for name, _ in service.calls] == expected_calls
        assert service.pending is None
        assert app.composer_value() == "保留问题"
        assert app.history == [{"role": "user", "content": "已确认历史"}]
        assert "重启" in app.last_notice
