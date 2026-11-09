"""Unified instance workspace editing uses explicit local-owner confirmation."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from textual.widgets import Button, TextArea

from personal_assistant.cli.tui.app import ConfigScreen, ConfirmScreen, WorkbenchApp
from personal_assistant.cli.tui.service import WorkbenchError, WorkbenchService
from personal_assistant.core.extensions.config import config_digest
from tests.unit.test_tui_app import FakeService, settled


@pytest.mark.asyncio
async def test_workspace_service_uses_distinct_save_apply_and_digest():
    calls = []

    async def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"config": {}, "application": {"state": "PENDING"}})

    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        config = {"workspace_id": "instance", "roots": []}
        await service.workspace()
        await service.save_workspace(config, "save-key")
        await service.apply_workspace("sha256:" + "a" * 64, "apply-key")
        assert [(r.method, r.url.path) for r in calls] == [
            ("GET", "/admin/v1/workspace"),
            ("PUT", "/admin/v1/workspace"),
            ("POST", "/admin/v1/workspace/apply"),
        ]
        assert json.loads(calls[1].content) == {"config": config}
        assert json.loads(calls[2].content) == {"expected_digest": "sha256:" + "a" * 64}
        assert calls[1].headers["Idempotency-Key"] == "save-key"
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_workspace_uncertain_save_cannot_overwrite_later_edits_by_replay():
    async def respond(request):
        raise httpx.ReadTimeout("lost", request=request)

    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkbenchError) as error:
            await service.save_workspace({"workspace_id": "instance", "roots": []}, "save")
        assert error.value.uncertain
        assert service.pending["retryable"] is False
    finally:
        await service.aclose()


async def wait_for_screen(app, screen_type):
    async def changed():
        while True:
            screen = app.screen
            selector = "#config-yes" if screen_type is ConfigScreen else "#confirm-yes"
            buttons = list(screen.query(selector)) if isinstance(screen, screen_type) else []
            if screen.is_mounted and buttons and all(button.is_mounted for button in buttons):
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(changed(), timeout=3)


class WorkspaceService(FakeService):
    async def workspace(self):
        config = {"workspace_id": "instance", "roots": []}
        return {
            "config": config,
            "application": {"saved_digest": config_digest(config), "state": "PENDING"},
        }

    async def save_workspace(self, config, key):
        self.calls.append(("workspace_save", config))
        return await self.workspace()

    async def apply_workspace(self, digest, key):
        self.calls.append(("workspace_apply", digest))
        value = await self.workspace()
        value["application"]["state"] = "APPLYING"
        return value


@pytest.mark.asyncio
async def test_workspace_edit_requires_confirmation_and_never_auto_applies():
    service = WorkspaceService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 42)):
        await settled(app)
        app._start("workspace-edit")
        await wait_for_screen(app, ConfigScreen)
        assert isinstance(app.screen, ConfigScreen)
        config = {
            "workspace_id": "instance",
            "roots": [
                {"key": "notes", "label": "资料", "path": "C:/synthetic", "mode": "read_write"}
            ],
        }
        app.screen.query_one(TextArea).load_text(json.dumps(config))
        app.screen.query_one("#config-yes", Button).press()
        await wait_for_screen(app, ConfirmScreen)
        assert isinstance(app.screen, ConfirmScreen)
        assert "read_write" in app.screen.body_text
        assert "授权" in app.screen.body_text
        assert not service.calls
        app.screen.query_one("#confirm-yes", Button).press()
        await settled(app)
        assert service.calls == [("workspace_save", config)]
        assert "待生效" in app.last_notice


@pytest.mark.asyncio
async def test_workspace_apply_binds_last_saved_digest_after_confirmation():
    service = WorkspaceService()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 42)):
        await settled(app)
        app._start("workspace-apply")
        await wait_for_screen(app, ConfirmScreen)
        assert isinstance(app.screen, ConfirmScreen)
        assert not service.calls
        app.screen.query_one("#confirm-yes", Button).press()
        await settled(app)
        assert service.calls == [
            (
                "workspace_apply",
                config_digest({"workspace_id": "instance", "roots": []}),
            )
        ]
        assert "应用中" in app.last_notice


def test_workspace_applied_does_not_claim_index_ready():
    label = WorkbenchApp._workspace_label({"application": {"state": "APPLIED"}})
    assert "已生效" in label
    assert "不代表索引已就绪" in label


def workspace_payload(config, **flags):
    return {
        "config": config,
        "application": {
            "saved_digest": config_digest(config),
            "state": "PENDING",
            **flags,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value, hint",
    [
        (
            {
                "config": {"workspace_id": "instance", "roots": []},
                "application": {
                    "saved_digest": config_digest(
                        {
                            "workspace_id": "instance",
                            "roots": [
                                {
                                    "key": "other",
                                    "label": "Other",
                                    "path": "C:/other",
                                    "mode": "read_write",
                                }
                            ],
                        }
                    ),
                    "state": "APPLIED",
                    "refresh_required": False,
                },
            },
            "重新读取",
        ),
        (
            workspace_payload({"workspace_id": "instance", "roots": []}, refresh_required=True),
            "重新读取",
        ),
        (
            workspace_payload({"workspace_id": "instance", "roots": []}, refresh_required="false"),
            "重新读取",
        ),
        (
            {"config": {"workspace_id": "instance", "roots": []}, "application": "invalid"},
            "重新读取",
        ),
        ({"config": [], "application": {"saved_digest": "sha256:" + "0" * 64}}, "重新读取"),
        (
            {
                "config": {"workspace_id": "instance", "roots": [], "number": float("nan")},
                "application": {"saved_digest": "sha256:" + "0" * 64},
            },
            "重新读取",
        ),
        (workspace_payload({"workspace_id": "instance", "roots": []}, state=[]), "重新读取"),
        (workspace_payload({}), "未保存"),
        (workspace_payload({"workspace_id": "other", "roots": []}), "重新读取"),
    ],
)
async def test_workspace_apply_rejects_unbound_or_malformed_material_without_confirmation(
    value,
    hint,
    monkeypatch,
):
    class Service(WorkspaceService):
        async def workspace(self):
            return value

    service = Service()
    app = WorkbenchApp(service, refresh_seconds=60)
    confirmations = []

    async def unexpected_confirm(screen):
        confirmations.append(screen)
        return False

    monkeypatch.setattr(app, "_confirm_remote", unexpected_confirm)
    async with app.run_test(size=(110, 42)):
        await settled(app)
        app._start("workspace-apply")
        await settled(app)
        assert confirmations == [], "unbound material must never reach owner confirmation"
        assert service.calls == []
        assert hint in app.last_notice
        assert "已生效" not in app.last_notice


@pytest.mark.asyncio
async def test_unsaved_workspace_editor_seeds_template_without_implicitly_saving():
    class Service(WorkspaceService):
        async def workspace(self):
            return workspace_payload({})

    service = Service()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 42)):
        await settled(app)
        app._start("workspace-edit")
        await wait_for_screen(app, ConfigScreen)
        assert json.loads(app.screen.query_one(TextArea).text) == {
            "workspace_id": "instance",
            "roots": [],
        }
        assert service.calls == []
        app.screen.query_one("#config-no", Button).press()
        await settled(app)
        assert service.calls == []


@pytest.mark.asyncio
async def test_workspace_malformed_apply_receipt_cannot_claim_success(monkeypatch):
    class Service(WorkspaceService):
        async def apply_workspace(self, digest, key):
            self.calls.append(("workspace_apply", digest))
            return {"application": {"state": "APPLIED"}}

    service = Service()
    app = WorkbenchApp(service, refresh_seconds=60)

    async def confirm(screen):
        return True

    monkeypatch.setattr(app, "_confirm_remote", confirm)
    async with app.run_test(size=(110, 42)):
        await settled(app)
        app._start("workspace-apply")
        await settled(app)
        assert len(service.calls) == 1
        assert "重新读取" in app.last_notice
        assert "已生效" not in app.last_notice
