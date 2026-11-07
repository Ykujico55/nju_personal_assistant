import pytest

from personal_assistant.cli.tui.app import WorkbenchApp


def test_config_presentation_does_not_claim_saved_is_applied():
    text = WorkbenchApp._configuration_label({"application": {"state": "PENDING"}})
    assert "待生效" in text
    assert "已生效" not in text
    text = WorkbenchApp._configuration_label({"application": {"state": "APPLYING"}})
    assert "等待各运行副本" in text
    assert "已生效" not in text
    text = WorkbenchApp._configuration_label({"application": {"state": "APPLIED"}})
    assert "已生效" in text and "索引需单独重建" in text


def test_apply_failure_shows_safe_code():
    text = WorkbenchApp._configuration_label(
        {
            "application": {
                "state": "FAILED",
                "runtimes": [{"error_code": "CONFIG_WORKER_START_FAILED"}],
            }
        }
    )
    assert "应用失败" in text and "CONFIG_WORKER_START_FAILED" in text


@pytest.mark.asyncio
async def test_apply_button_submits_saved_digest_without_disable_enable():
    from tests.unit.test_tui_app import FakeService, settled

    class Service(FakeService):
        async def extension_config(self, identifier):
            return {
                "extension_version": "1.0.0",
                "application": {
                    "saved_digest": "sha256:" + "a" * 64,
                    "state": "PENDING",
                },
            }

        async def apply_extension_config(self, identifier, version, digest, key):
            self.calls.append(("apply", (identifier, version, digest)))
            return {"application": {"state": "APPLYING"}}

    service = Service()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 40)):
        await settled(app)
        app.selected_extension_id = "fixture.ext"
        app._start("extension-apply-config")
        await settled(app)
        assert service.calls == [("apply", ("fixture.ext", "1.0.0", "sha256:" + "a" * 64))]
        assert "应用中" in app.last_notice


@pytest.mark.asyncio
async def test_missing_extension_configuration_does_not_stop_other_panel_refresh():
    from personal_assistant.cli.tui.service import WorkbenchError
    from tests.unit.test_tui_app import FakeService, settled

    class Service(FakeService):
        async def extension_config(self, identifier):
            raise WorkbenchError("EXTENSION_NOT_FOUND")

        async def task(self, task_id):
            self.calls.append(("task", task_id))
            return await super().task(task_id)

    service = Service()
    app = WorkbenchApp(service, refresh_seconds=60)
    async with app.run_test(size=(110, 40)):
        await settled(app)
        app.selected_extension_id = "fixture.removed"
        app.selected_task_id = "task_fixture"
        await app._refresh()
        assert ("task", "task_fixture") in service.calls
        assert "部分读取失败" not in app.status_text
