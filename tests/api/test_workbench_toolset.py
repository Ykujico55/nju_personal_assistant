from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from personal_assistant.admin_app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.domain import ApprovalState, RiskLevel, TaskRun, TaskState, ToolDescriptor
from personal_assistant.settings import Settings

PREFIX = "/admin/v1/workbench"


def descriptor(**changes):
    base = ToolDescriptor(
        id="fixture.read", version="1", extension_id="fixture", extension_version="1",
        risk=RiskLevel.READ, input_schema={"type": "object"}, output_schema={"type": "object"},
    )
    return replace(base, **changes)


def pinned(**changes):
    return {"version": "1", "extension_version": "1", "grants": [], **changes}


@pytest.fixture
async def workbench(tmp_path, monkeypatch):
    with patch.dict(os.environ, {}, clear=True):
        settings = replace(Settings.from_env(), extension_root=tmp_path / "extensions",
                           artifact_root=tmp_path / "artifacts")
    container = build_container(settings)
    # The isolated fixture supplies an immutable catalog; no extension is imported.
    monkeypatch.setattr(type(container), "refresh_tool_registry", AsyncMock())
    app = create_app(settings=settings, container=container)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8001",
        headers={"X-Assistant-Workbench": "1"},
    ) as client:
        yield container, client
    await container.aclose()


async def seeded(container, snapshot, *, state=TaskState.WAITING_USER):
    task = await container.tasks.create(objective="original goal", idempotency_key="fixture-task")
    await container.tasks.project(task.id, state)
    run = TaskRun(id="run_" + task.id, task_id=task.id, objective=task.objective)
    await container.checkpoint_store.save(run, snapshot)
    return task.id


@pytest.mark.parametrize("snapshot,expected", [({}, "UNBOUND"), ({"tools": {}}, "CHANGED")])
async def test_no_snapshot_and_explicit_empty_tools_are_distinct(workbench, snapshot, expected):
    container, client = workbench
    container.tool_registry.publish([descriptor()])
    task_id = await seeded(container, snapshot)
    before = await container.checkpoint_store.load("run_" + task_id)
    response = await client.get(f"{PREFIX}/tasks/{task_id}/trace")
    assert response.status_code == 200
    value = response.json()["toolset"]
    assert value["state"] == expected
    assert value["can_recreate"] is True
    assert [item["id"] for item in value["added"]] == (
        ["fixture.read"] if expected == "CHANGED" else []
    )
    assert await container.checkpoint_store.load("run_" + task_id) == before


async def test_unchanged_and_empty_bound_toolsets_are_bound(workbench):
    container, client = workbench
    task_id = await seeded(container, {"tools": {}})
    response = await client.get(f"{PREFIX}/tasks/{task_id}/trace")
    assert response.json()["toolset"]["state"] == "BOUND"
    container.tool_registry.publish([descriptor()])
    await container.checkpoint_store.save(
        TaskRun(id="run_" + task_id, task_id=task_id, objective="original goal"),
        {"tools": {"fixture.read": pinned()}},
    )
    value = (await client.get(f"{PREFIX}/tasks/{task_id}/trace")).json()["toolset"]
    assert value["state"] == "BOUND"
    assert value["added"] == value["unavailable"] == []


@pytest.mark.parametrize("current,bound,reason", [
    ([], pinned(), "TOOL_UNAVAILABLE"),
    ([descriptor(version="2")], pinned(), "TOOL_VERSION_CHANGED"),
    ([descriptor(extension_version="2")], pinned(), "EXTENSION_VERSION_CHANGED"),
    ([descriptor()], pinned(grants=["fixture.read"]), "GRANTS_CHANGED"),
    ([descriptor(extension_id="other")], pinned(extension_id="fixture"), "EXTENSION_CHANGED"),
])
async def test_bound_identity_changes_are_reported_without_expanding_old_tools(
    workbench, current, bound, reason,
):
    container, client = workbench
    container.tool_registry.publish(current)
    task_id = await seeded(container, {"tools": {"fixture.read": bound},
                                       "private_input": "never-return"})
    response = await client.get(f"{PREFIX}/tasks/{task_id}/trace")
    value = response.json()["toolset"]
    assert value["state"] == "CHANGED"
    assert value["unavailable"][0]["id"] == "fixture.read"
    assert value["unavailable"][0]["reason"] == reason
    assert value["unavailable"][0]["extension_id"] == bound.get("extension_id")
    assert "never-return" not in response.text
    saved = await container.checkpoint_store.load("run_" + task_id)
    assert saved["tools"] == {"fixture.read": bound}


@pytest.mark.parametrize("state", [TaskState.CREATED, TaskState.QUEUED, TaskState.RUNNING,
                                    TaskState.WAITING_APPROVAL, TaskState.WAITING_RECONCILIATION])
async def test_active_or_unresolved_tasks_cannot_offer_recreation(workbench, state):
    container, client = workbench
    task_id = await seeded(container, {"tools": {}}, state=state)
    value = (await client.get(f"{PREFIX}/tasks/{task_id}/trace")).json()["toolset"]
    assert value["can_recreate"] is False


@pytest.mark.parametrize("risk", ["R2", "EXTERNAL_WRITE"])
async def test_unknown_external_call_blocks_recreation_in_trace_and_execution(workbench, risk):
    container, client = workbench
    snapshot = {"tools": {}, "risk": risk, "phase": "waiting", "unresolved_external": True,
                "pending_call": {"tool_id": "fixture.send", "arguments": {"secret": "private"}}}
    task_id = await seeded(container, snapshot, state=TaskState.FAILED)
    for endpoint in ("trace", "execution"):
        response = await client.get(f"{PREFIX}/tasks/{task_id}/{endpoint}")
        assert response.status_code == 200
        assert response.json()["toolset"]["can_recreate"] is False
        assert response.json()["requires_reconciliation"] is True
        assert "private" not in response.text


async def test_pending_unexecuted_external_approval_also_blocks_recreation(workbench):
    container, client = workbench
    container.approvals.get = AsyncMock(return_value=SimpleNamespace(state=ApprovalState.APPROVED))
    task_id = await seeded(container, {
        "tools": {}, "risk": "EXTERNAL_WRITE", "phase": "waiting",
        "pending_call": {"tool_id": "fixture.send", "approval_id": "approval-fixture"},
    })
    response = await client.get(f"{PREFIX}/tasks/{task_id}/trace")
    assert response.json()["requires_reconciliation"] is False
    assert response.json()["toolset"]["can_recreate"] is False


async def test_private_execution_adds_projection_without_altering_shared_execution(workbench):
    container, client = workbench
    container.tool_registry.publish([descriptor()])
    task_id = await seeded(container, {"tools": {}})
    value = (await client.get(f"{PREFIX}/tasks/{task_id}/execution")).json()
    assert value["state"] == "WAITING_USER"
    assert value["toolset"]["state"] == "CHANGED"
    assert value["toolset"]["can_recreate"] is True
    assert value["requires_reconciliation"] is False
    assert "execution" in value and "task_version" in value
