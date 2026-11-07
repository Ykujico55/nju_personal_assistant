from __future__ import annotations

import pytest

from personal_assistant import __version__
from tests.api.test_workbench_toolset import PREFIX
from tests.api.test_workbench_toolset import workbench as workbench
from tests.unit.test_tui_capabilities import FEATURES


async def test_capabilities_are_explicit_local_and_read_only(workbench):
    container, client = workbench
    assert await container.tasks.list_recent(limit=10) == ()
    response = await client.get(PREFIX + "/capabilities")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "protocol_version": 1, "server_version": __version__,
        "features": sorted([*FEATURES, "task_reconciliation"]),
    }
    assert await container.tasks.list_recent(limit=10) == ()


@pytest.mark.parametrize("headers", [
    {"X-Assistant-Workbench": ""}, {"Origin": "https://external.example"},
    {"Host": "external.example"},
])
async def test_capabilities_keep_existing_loopback_guard(workbench, headers):
    _, client = workbench
    response = await client.get(PREFIX + "/capabilities", headers=headers)
    assert response.status_code == 403
