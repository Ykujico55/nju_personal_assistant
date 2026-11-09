"""Workspace application across independent supervisors, without real private files."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from personal_assistant.core.extensions import ExtensionRecord, ExtensionState, ManifestParser
from personal_assistant.core.extensions.config import ExtensionConfigError
from personal_assistant.core.extensions.errors import ExtensionOperationError
from personal_assistant.infrastructure.extensions.config_store import FileExtensionConfigStore
from personal_assistant.infrastructure.extensions.processes import ProcessRuntimeSupervisor
from personal_assistant.infrastructure.extensions.workspace import WorkspaceExtensionConfigStore
from tests.unit.test_extension_config_application import ID, Worker
from tests.unit.test_extension_supervision_service import EXAMPLE


class WorkspaceWorker(Worker):
    async def start(self, *, non_secret_config, timeout_seconds, workspace=None):
        await super().start(non_secret_config=non_secret_config, timeout_seconds=timeout_seconds)
        self.workspace = workspace

    async def health(self, **kwargs):
        return {"healthy": bool(self.workspace and self.workspace.get("roots"))}

    async def call_slot(self, method, parameters, **kwargs):
        return self.workspace

    async def invoke_tool(self, *args, **kwargs):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return {"status": "UNKNOWN", "workspace": self.workspace}


def document(root: Path):
    return {"workspace_id": "instance", "roots": [
        {"key": "notes", "label": "Notes", "path": str(root), "mode": "read_write"}
    ]}


async def setup_runtime(tmp_path, monkeypatch, *, explicit_opt_in=True):
    schema_root = tmp_path / "package"
    schema_root.mkdir()
    properties = {"workspace_binding": {"type": "string", "enum": ["instance"]}}
    (schema_root / "config.json").write_text(json.dumps({
        "type": "object", "properties": properties if explicit_opt_in else {},
        "additionalProperties": True,
    }), encoding="utf-8")
    record = ExtensionRecord(
        manifest=replace(ManifestParser().parse(EXAMPLE), root=schema_root,
                         config_schema="config.json"),
        artifact_hash="sha256:" + "0" * 64,
        state=ExtensionState.ENABLED,
        install_path=str(EXAMPLE),
    )
    stores = [WorkspaceExtensionConfigStore(
        FileExtensionConfigStore(tmp_path / "config"), tmp_path / "workspace"
    ) for _ in range(2)]
    await stores[0].save(ID, {"workspace_binding": "instance"})
    await stores[0].apply(ID, (await stores[0].status(ID))["saved_digest"])
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()
    await apply_workspace(stores[0], document(roots[0]))
    workers = []

    def build(manifest, *args, **kwargs):
        worker = WorkspaceWorker(manifest)
        workers.append(worker)
        return worker

    monkeypatch.setattr(
        "personal_assistant.infrastructure.extensions.processes._build_worker", build
    )
    instances = [ProcessRuntimeSupervisor(config_store=store) for store in stores]
    return stores, instances, record, workers, roots


async def apply_workspace(store, config):
    saved = await store.save_workspace(config)
    return await store.apply_workspace(saved["application"]["saved_digest"])


async def test_shared_apply_refreshes_every_replica_only_after_apply(tmp_path, monkeypatch):
    stores, instances, record, workers, roots = await setup_runtime(tmp_path, monkeypatch)
    try:
        for runtime in instances:
            await runtime.start(record)
        initial = workers[0].workspace
        assert initial["roots"][0]["path"] == str(roots[0])
        assert workers[1].workspace == initial
        await stores[0].save_workspace(document(roots[1]))
        for runtime in instances:
            await runtime.synchronize_configurations()
        assert len(workers) == 2
        await stores[0].apply_workspace(
            (await stores[0].get_workspace())["application"]["saved_digest"]
        )
        assert (await stores[0].status(ID))["state"] != "APPLIED"
        for runtime in instances:
            await runtime.synchronize_configurations()
        assert len(workers) == 4
        assert all(not worker.client.running for worker in workers[:2])
        assert workers[2].workspace == workers[3].workspace
        assert workers[2].workspace["revision"] != initial["revision"]
        assert workers[2].workspace["roots"][0]["path"] == str(roots[1])
        assert (await stores[0].status(ID))["state"] == "APPLIED"
        for runtime in instances:
            await runtime.synchronize_configurations()
        assert len(workers) == 4  # unchanged generation never restarts
    finally:
        for runtime in instances:
            await runtime.stop_all()


@pytest.mark.parametrize("risk", ["READ", "EXTERNAL_WRITE"])
async def test_workspace_apply_waits_and_never_replays_inflight_call(tmp_path, monkeypatch, risk):
    stores, instances, record, workers, roots = await setup_runtime(tmp_path, monkeypatch)
    record = replace(record, manifest=replace(record.manifest, tools=tuple(
        replace(tool, risk=risk) for tool in record.manifest.tools
    )))
    runtime = instances[0]
    call = sync = None
    try:
        await runtime.start(record)
        old = workers[0]
        old.release.clear()
        call = asyncio.create_task(runtime.invoke_tool(
            ID, "example.echo", {}, task_id="t", run_id="r",
            idempotency_key="unchanged-command", deadline_seconds=10,
        ))
        await asyncio.wait_for(old.entered.wait(), 2)
        await apply_workspace(stores[0], document(roots[1]))
        sync = asyncio.create_task(runtime.synchronize_configurations())
        await asyncio.sleep(0)
        assert not sync.done() and not call.done() and old.client.running
        old.release.set()
        if risk == "READ":
            with pytest.raises(ExtensionOperationError, match="configuration changed"):
                await call
        else:
            result = await call
            assert result["status"] == "UNKNOWN"
            assert result["workspace"] == old.workspace
        await sync
        assert old.calls == 1
        assert (await runtime.call_slot(ID, "context.retrieve", {}))["roots"][0]["path"] == str(
            roots[1]
        )
        assert sum(worker.calls for worker in workers) == 1
    finally:
        for worker in workers:
            worker.release.set()
        await asyncio.gather(*[task for task in (call, sync) if task], return_exceptions=True)
        for item in instances:
            await item.stop_all()


async def test_revocation_closes_old_worker_without_using_legacy_roots(tmp_path, monkeypatch):
    stores, instances, record, workers, _ = await setup_runtime(tmp_path, monkeypatch)
    runtime = instances[0]
    try:
        await runtime.start(record)
        await apply_workspace(stores[0], {"workspace_id": "instance", "roots": []})
        await runtime.synchronize_configurations()
        assert all(not worker.client.running for worker in workers)
        assert (await stores[0].status(ID))["state"] == "FAILED"
        count = len(workers)
        with pytest.raises(ExtensionOperationError):
            await runtime.call_slot(ID, "context.retrieve", {})
        await runtime.synchronize_configurations()
        assert len(workers) == count
    finally:
        for item in instances:
            await item.stop_all()


async def test_schema_must_explicitly_opt_in_before_receiving_workspace(tmp_path, monkeypatch):
    _, instances, record, workers, _ = await setup_runtime(
        tmp_path, monkeypatch, explicit_opt_in=False
    )
    try:
        with pytest.raises((ExtensionConfigError, ExtensionOperationError)):
            await instances[0].start(record)
        assert not workers  # never share paths with a legacy extension
    finally:
        for item in instances:
            await item.stop_all()


@pytest.mark.parametrize("phase", ["health", "runtime_report"])
async def test_workspace_changed_during_handshake_never_dispatches_to_stale_worker(
    tmp_path, monkeypatch, phase
):
    stores, instances, record, workers, roots = await setup_runtime(tmp_path, monkeypatch)
    record = replace(record, manifest=replace(record.manifest, tools=tuple(
        replace(tool, risk="INTERNAL_WRITE") for tool in record.manifest.tools
    )))
    runtime = instances[0]
    entered, release = asyncio.Event(), asyncio.Event()
    original = WorkspaceWorker.health

    async def held_health(worker, **kwargs):
        entered.set()
        await release.wait()
        return await original(worker, **kwargs)

    call = None
    try:
        await runtime.start(record)
        await apply_workspace(stores[0], document(roots[1]))
        if phase == "health":
            monkeypatch.setattr(WorkspaceWorker, "health", held_health)
        else:
            original_report = stores[0].report_runtime

            async def held_report(*args, **kwargs):
                await original_report(*args, **kwargs)
                if kwargs["state"] == "RUNNING":
                    entered.set()
                    await release.wait()

            monkeypatch.setattr(stores[0], "report_runtime", held_report)
        call = asyncio.create_task(runtime.invoke_tool(
            ID, "example.echo", {}, task_id="t", run_id="r", idempotency_key="stale-start",
            deadline_seconds=10,
        ))
        await asyncio.wait_for(entered.wait(), 2)
        await apply_workspace(stores[0], document(roots[0]))
        release.set()
        with pytest.raises(ExtensionOperationError):
            await call
        assert sum(worker.calls for worker in workers) == 0
        assert all(not worker.client.running for worker in workers)
        result = await runtime.invoke_tool(
            ID, "example.echo", {}, task_id="t", run_id="r", idempotency_key="new-request",
            deadline_seconds=10,
        )
        assert result["workspace"]["roots"][0]["path"] == str(roots[0])
        assert sum(worker.calls for worker in workers) == 1
    finally:
        release.set()
        if call is not None:
            await asyncio.gather(call, return_exceptions=True)
        for instance in instances:
            await instance.stop_all()
