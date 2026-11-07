from __future__ import annotations

import pytest

from tests.api.test_workbench_toolset import PREFIX, seeded
from tests.api.test_workbench_toolset import workbench as workbench

SENTINEL = "private-error-fixture-never-return"
CORRELATION = "diag_" + "a" * 32


async def test_trace_rebuilds_failure_diagnostic_and_leaves_checkpoint_unchanged(workbench):
    container, client = workbench
    stored = {
        "code": "TOOL_UNAVAILABLE", "correlation_id": CORRELATION,
        "message": SENTINEL, "arguments": {"private": SENTINEL},
        "credential": SENTINEL, "traceback": SENTINEL,
    }
    task_id = await seeded(container, {"failure_diagnostic": stored})
    before = await container.checkpoint_store.load("run_" + task_id)
    response = await client.get(f"{PREFIX}/tasks/{task_id}/trace")
    assert response.status_code == 200
    assert response.json()["failure_diagnostic"] == {
        "code": "TOOL_UNAVAILABLE", "correlation_id": CORRELATION,
        "message": "当前工具或版本不可用；这不代表目录未配置，请核对固定工具清单。",
    }
    assert SENTINEL not in response.text
    assert "credential" not in response.text and "traceback" not in response.text
    assert await container.checkpoint_store.load("run_" + task_id) == before


@pytest.mark.parametrize("stored", [
    {"code": SENTINEL, "correlation_id": CORRELATION, "message": SENTINEL},
    {"code": "TOOL_UNAVAILABLE", "correlation_id": SENTINEL, "message": SENTINEL},
    {"code": "TOOL_UNAVAILABLE", "correlation_id": "diag_bad", "message": SENTINEL},
    SENTINEL,
])
async def test_trace_omits_untrusted_failure_diagnostic(workbench, stored):
    container, client = workbench
    task_id = await seeded(container, {"failure_diagnostic": stored})
    response = await client.get(f"{PREFIX}/tasks/{task_id}/trace")
    assert response.status_code == 200
    assert "failure_diagnostic" not in response.json()
    assert SENTINEL not in response.text
