"""Configuration save/apply and independent runtime acknowledgement."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from personal_assistant.core.extensions import ExtensionRecord, ExtensionState, ManifestParser
from personal_assistant.core.extensions.config import ExtensionConfigError
from personal_assistant.core.extensions.errors import ExtensionOperationError, RpcCallError
from personal_assistant.infrastructure.extensions.config_store import FileExtensionConfigStore
from personal_assistant.infrastructure.extensions.processes import ProcessRuntimeSupervisor
from tests.unit.test_extension_supervision_service import EXAMPLE

ID = "example.echo"


def legacy(tmp_path):
    (tmp_path / f"{ID}.json").write_text(json.dumps({"prefix": "old"}), encoding="utf-8")
    return FileExtensionConfigStore(tmp_path)


async def test_save_is_pending_until_explicit_digest_bound_apply(tmp_path):
    store = legacy(tmp_path)
    before = await store.get_applied(ID)
    await store.save(ID, {"prefix": "new"})
    assert (await store.get(ID))["prefix"] == "new"
    assert await store.get_applied(ID) == before
    status = await store.status(ID)
    assert status["state"] == "PENDING"
    assert status["saved_digest"] != status["desired_digest"]
    applied = await store.apply(ID, status["saved_digest"])
    assert (await store.get_applied(ID))["config"] == {"prefix": "new"}
    assert applied["state"] == "PENDING"  # No runtime has confirmed loading it.
    repeated = await store.apply(ID, status["saved_digest"])
    assert repeated["generation"] == applied["generation"]


async def test_stale_saved_digest_is_rejected_without_changing_active_snapshot(tmp_path):
    left, right = legacy(tmp_path), FileExtensionConfigStore(tmp_path)
    await left.save(ID, {"prefix": "one"})
    expected = (await left.status(ID))["saved_digest"]
    before = await left.get_applied(ID)
    await right.save(ID, {"prefix": "two"})
    with pytest.raises(ExtensionConfigError):
        await left.apply(ID, expected)
    assert await left.get_applied(ID) == before


async def test_live_runtime_must_ack_exact_generation_and_status_contains_no_config(tmp_path):
    left, right = legacy(tmp_path), FileExtensionConfigStore(tmp_path)
    old = await left.get_applied(ID)
    await left.report_runtime(
        ID, "a" * 32, loaded_digest=old["digest"], generation=old["generation"], state="RUNNING"
    )
    try:
        await right.save(ID, {"prefix": "private-path-value"})
        target = await right.apply(ID, (await right.status(ID))["saved_digest"])
        assert target["state"] == "APPLYING"
        assert "private-path-value" not in json.dumps(target)
        await left.report_runtime(
            ID,
            "a" * 32,
            loaded_digest=target["desired_digest"],
            generation=target["generation"],
            state="RUNNING",
        )
        assert (await right.status(ID))["state"] == "APPLIED"
    finally:
        await left.release_runtime("a" * 32)
    assert (await right.status(ID))["state"] == "PENDING"


class Worker:
    def __init__(self, manifest):
        self.manifest = manifest
        self.client = SimpleNamespace(running=True)
        self.config = {}
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def start(self, *, non_secret_config, timeout_seconds):
        self.config = dict(non_secret_config)

    async def health(self, **kwargs):
        return {"healthy": self.config.get("prefix") != "bad"}

    async def close(self):
        self.client.running = False

    async def call_slot(self, method, parameters, **kwargs):
        return self.config

    async def invoke_tool(self, *args, **kwargs):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return {"output": self.config}


def runtimes(tmp_path, monkeypatch):
    workers = []

    def build(manifest, *args, **kwargs):
        worker = Worker(manifest)
        workers.append(worker)
        return worker

    monkeypatch.setattr(
        "personal_assistant.infrastructure.extensions.processes._build_worker", build
    )
    stores = [legacy(tmp_path), FileExtensionConfigStore(tmp_path)]
    instances = [ProcessRuntimeSupervisor(config_store=store) for store in stores]
    record = ExtensionRecord(
        manifest=ManifestParser().parse(EXAMPLE),
        artifact_hash="sha256:" + "0" * 64,
        state=ExtensionState.ENABLED,
        install_path=str(EXAMPLE),
    )
    return stores, instances, record, workers


async def test_each_runtime_restarts_on_apply_but_never_on_save(tmp_path, monkeypatch):
    stores, instances, record, workers = runtimes(tmp_path, monkeypatch)
    try:
        for runtime in instances:
            await runtime.start(record)
        await stores[0].save(ID, {"prefix": "new"})
        for runtime in instances:
            await runtime.synchronize_configurations()
        assert len(workers) == 2
        target = await stores[0].apply(ID, (await stores[0].status(ID))["saved_digest"])
        assert target["state"] == "APPLYING"
        for runtime in instances:
            await runtime.synchronize_configurations()
        assert len(workers) == 4
        assert all(not worker.client.running for worker in workers[:2])
        assert (await stores[0].status(ID))["state"] == "APPLIED"
    finally:
        for runtime in instances:
            await runtime.stop_all()


@pytest.mark.parametrize("risk", ["READ", "EXTERNAL_WRITE"])
async def test_apply_waits_for_inflight_call_without_cancellation_or_replay(
    tmp_path, monkeypatch, risk
):
    stores, instances, record, workers = runtimes(tmp_path, monkeypatch)
    runtime = instances[0]
    record = replace(
        record,
        manifest=replace(
            record.manifest, tools=tuple(replace(tool, risk=risk) for tool in record.manifest.tools)
        ),
    )
    await runtime.start(record)
    old = workers[0]
    old.release.clear()
    call = asyncio.create_task(
        runtime.invoke_tool(
            ID,
            "example.echo",
            {},
            task_id="t",
            run_id="r",
            idempotency_key="once",
            deadline_seconds=10,
        )
    )
    sync = None
    try:
        await asyncio.wait_for(old.entered.wait(), 2)
        await stores[0].save(ID, {"prefix": "new"})
        await stores[0].apply(ID, (await stores[0].status(ID))["saved_digest"])
        sync = asyncio.create_task(runtime.synchronize_configurations())
        for _ in range(10):
            await asyncio.sleep(0)
        assert not call.done() and not sync.done()
        assert old.client.running
        old.release.set()
        if risk == "EXTERNAL_WRITE":
            assert await call == {"output": {"prefix": "old"}}
        else:
            with pytest.raises(ExtensionOperationError, match="configuration changed"):
                await call
        await sync
        assert old.calls == 1
        assert (await runtime.call_slot(ID, "context.retrieve", {})) == {"prefix": "new"}
    finally:
        old.release.set()
        await asyncio.gather(call, *([sync] if sync else []), return_exceptions=True)
        for item in instances:
            await item.stop_all()


async def test_failed_generation_is_visible_and_not_retried_or_served_as_old(tmp_path, monkeypatch):
    stores, instances, record, workers = runtimes(tmp_path, monkeypatch)
    runtime = instances[0]
    try:
        await runtime.start(record)
        await stores[0].save(ID, {"prefix": "bad"})
        await stores[0].apply(ID, (await stores[0].status(ID))["saved_digest"])
        await runtime.synchronize_configurations()
        assert (await stores[0].status(ID))["state"] == "FAILED"
        assert all(not worker.client.running for worker in workers)
        count = len(workers)
        await runtime.synchronize_configurations()
        with pytest.raises(ExtensionOperationError):
            await runtime.call_slot(ID, "context.retrieve", {})
        assert len(workers) == count
    finally:
        for item in instances:
            await item.stop_all()


async def test_new_live_replica_without_health_ack_keeps_status_applying(tmp_path, monkeypatch):
    stores, instances, record, workers = runtimes(tmp_path, monkeypatch)
    await instances[0].start(record)
    assert (await stores[0].status(ID))["state"] == "APPLIED"
    entered, release = asyncio.Event(), asyncio.Event()
    original = Worker.health

    async def wait_health(worker, **kwargs):
        entered.set()
        await release.wait()
        return await original(worker, **kwargs)

    monkeypatch.setattr(Worker, "health", wait_health)
    joining = asyncio.create_task(instances[1].start(record))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        status = await stores[0].status(ID)
        assert len(status["runtimes"]) == 2
        assert status["state"] == "APPLYING"
        release.set()
        await joining
        assert (await stores[0].status(ID))["state"] == "APPLIED"
    finally:
        release.set()
        await asyncio.gather(joining, return_exceptions=True)
        for item in instances:
            await item.stop_all()


@pytest.mark.parametrize("state", [ExtensionState.DISABLED, ExtensionState.QUARANTINED])
async def test_background_configuration_sync_does_not_revive_changed_lifecycle(
    tmp_path, monkeypatch, state
):
    stores, instances, record, workers = runtimes(tmp_path, monkeypatch)
    durable = record

    async def load_record(extension_id):
        assert extension_id == ID
        return durable

    runtime = ProcessRuntimeSupervisor(config_store=stores[0], record_reader=load_record)
    try:
        await runtime.start(record)
        durable = replace(record, state=state)
        await stores[0].save(ID, {"prefix": "new"})
        await stores[0].apply(ID, (await stores[0].status(ID))["saved_digest"])
        await runtime.synchronize_configurations()
        assert len(workers) == 1
        assert not workers[0].client.running
        with pytest.raises(ExtensionOperationError):
            await runtime.start(record)
        with pytest.raises(RpcCallError):
            await runtime.call_slot(ID, "context.retrieve", {})
        assert len(workers) == 1
    finally:
        await runtime.stop_all()


async def test_background_sync_never_restarts_broken_worker_or_old_upgraded_version(
    tmp_path, monkeypatch
):
    stores, instances, record, workers = runtimes(tmp_path, monkeypatch)
    durable = record

    async def load_record(extension_id):
        return durable

    runtime = ProcessRuntimeSupervisor(config_store=stores[0], record_reader=load_record)
    try:
        await runtime.start(record)
        workers[0].client.running = False
        await runtime.synchronize_configurations()
        assert len(workers) == 1
        assert (await stores[0].status(ID))["state"] == "FAILED"
        durable = replace(record, manifest=replace(record.manifest, version="0.2.0"))
        await stores[0].save(ID, {"prefix": "new"})
        await stores[0].apply(ID, (await stores[0].status(ID))["saved_digest"])
        await runtime.synchronize_configurations()
        assert len(workers) == 1
        with pytest.raises(ExtensionOperationError):
            await runtime.start(record)
        assert len(workers) == 1
    finally:
        await runtime.stop_all()


@pytest.mark.parametrize(
    "state", [ExtensionState.STARTING, ExtensionState.DRAINING, ExtensionState.UPGRADING]
)
async def test_configuration_loop_does_not_interrupt_lifecycle_transition(
    tmp_path, monkeypatch, state
):
    stores, instances, record, workers = runtimes(tmp_path, monkeypatch)
    durable = replace(record, state=ExtensionState.STARTING)

    async def load_record(extension_id):
        return durable

    runtime = ProcessRuntimeSupervisor(config_store=stores[0], record_reader=load_record)
    try:
        await runtime.start(durable)
        durable = replace(record, state=state)
        await stores[0].save(ID, {"prefix": "new"})
        await stores[0].apply(ID, (await stores[0].status(ID))["saved_digest"])
        await runtime.synchronize_configurations()
        assert len(workers) == 1 and workers[0].client.running
        with pytest.raises(ExtensionOperationError):
            await runtime.call_slot(ID, "context.retrieve", {})
        durable = record
        await runtime.synchronize_configurations()
        assert len(workers) == 2 and not workers[0].client.running
    finally:
        await runtime.stop_all()
