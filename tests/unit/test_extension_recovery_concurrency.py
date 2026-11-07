"""Independent supervisor recovery against the same durable-store contract."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from personal_assistant.core.extensions import ExtensionRecord, ExtensionRegistry, ExtensionState
from personal_assistant.core.extensions.lifecycle import LifecycleManager
from personal_assistant.core.extensions.operations import OperationState
from personal_assistant.core.extensions.supervision import ExtensionSupervisorService
from personal_assistant.infrastructure.memory.extensions import InMemoryLifecycleStore
from personal_assistant.infrastructure.memory.operations import InMemoryExtensionOperationStore
from tests.unit.test_extension_supervision_service import EXAMPLE, FakeRuntime, ManifestParser


class PausingRuntime(FakeRuntime):
    def __init__(self, boundary="start"):
        super().__init__()
        self.boundary = boundary
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def start(self, record):
        if self.boundary == "start":
            self.entered.set()
            await self.release.wait()
        await super().start(record)

    async def drain(self, record, deadline_epoch):
        if self.boundary == "drain":
            self.entered.set()
            await self.release.wait()
        return await super().drain(record, deadline_epoch)


def service(store, operations, runtime):
    registry = ExtensionRegistry()
    manager = LifecycleManager(store, registry, runtime, AsyncMock(), AsyncMock(), AsyncMock())
    return ExtensionSupervisorService(
        coordinator=AsyncMock(),
        manager=manager,
        registry=registry,
        store=store,
        operations=operations,
        runtime=runtime,
    )


async def seed(store, state=ExtensionState.ENABLED):
    record = ExtensionRecord(
        manifest=ManifestParser().parse(EXAMPLE),
        artifact_hash="sha256:" + "0" * 64,
        state=state,
        install_path=str(EXAMPLE),
    )
    await store.save(record)
    return record


async def settled_turns():
    # No elapsed-time race: allow the second independent supervisor to reach
    # either the store coordination gate or the old transient-state write.
    for _ in range(12):
        await asyncio.sleep(0)


async def test_two_independent_recoveries_do_not_quarantine_live_starting():
    store, operations = InMemoryLifecycleStore(), InMemoryExtensionOperationStore()
    record = await seed(store)
    first_runtime = PausingRuntime()
    first = service(store, operations, first_runtime)
    second = service(store, operations, FakeRuntime())
    first_task = asyncio.create_task(first.recover())
    second_task = None
    try:
        await asyncio.wait_for(first_runtime.entered.wait(), 2)
        second_task = asyncio.create_task(second.recover())
        await settled_turns()
        current = await store.get(record.manifest.id)
        assert current.state is ExtensionState.STARTING
        assert not second_task.done()
        first_runtime.release.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), 2)
        assert (await store.get(record.manifest.id)).state is ExtensionState.ENABLED
        assert first._registry.snapshot.owner_of("example.echo") is not None
        assert second._registry.snapshot.owner_of("example.echo") is not None
    finally:
        first_runtime.release.set()
        await asyncio.gather(
            first_task, *([second_task] if second_task else []), return_exceptions=True
        )


@pytest.mark.parametrize("kind", ["enable", "disable"])
async def test_recovery_does_not_interrupt_another_live_lifecycle_operation(kind):
    store, operations = InMemoryLifecycleStore(), InMemoryExtensionOperationStore()
    record = await seed(
        store, ExtensionState.DISABLED if kind == "enable" else ExtensionState.ENABLED
    )
    runtime = PausingRuntime("start" if kind == "enable" else "drain")
    owner = service(store, operations, runtime)
    newcomer = service(store, operations, FakeRuntime())
    operation = await getattr(owner, "begin_" + kind)(record.manifest.id)
    recovery = None
    try:
        await asyncio.wait_for(runtime.entered.wait(), 2)
        recovery = asyncio.create_task(newcomer.recover())
        await settled_turns()
        assert (await operations.get(operation.id)).status is OperationState.RUNNING
        assert not recovery.done()
        runtime.release.set()
        final = await owner.wait_operation(operation.id, timeout_seconds=2)
        await asyncio.wait_for(recovery, 2)
        assert final.status is OperationState.SUCCEEDED
        expected = ExtensionState.ENABLED if kind == "enable" else ExtensionState.DISABLED
        assert (await store.get(record.manifest.id)).state is expected
    finally:
        runtime.release.set()
        await asyncio.gather(
            *(list(owner._tasks) + ([recovery] if recovery else [])), return_exceptions=True
        )


async def test_orphan_starting_is_still_quarantined():
    store, operations = InMemoryLifecycleStore(), InMemoryExtensionOperationStore()
    record = await seed(store, ExtensionState.STARTING)
    runtime = FakeRuntime()
    await service(store, operations, runtime).recover()
    assert (await store.get(record.manifest.id)).state is ExtensionState.QUARANTINED
    assert runtime.started == []


async def test_cancelled_unclaimed_command_cannot_execute_after_gate_is_released():
    store, operations = InMemoryLifecycleStore(), InMemoryExtensionOperationStore()
    record = await seed(store, ExtensionState.DISABLED)
    runtime = FakeRuntime()
    owner = service(store, operations, runtime)
    async with store.operation_lock():
        request = asyncio.create_task(owner.begin_enable(record.manifest.id))
        await settled_turns()
        assert not request.done()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    await settled_turns()
    assert runtime.started == []
    assert (await store.get(record.manifest.id)).state is ExtensionState.DISABLED
    assert owner._active == set()
    assert owner._tasks == set()
    assert operations._operations == {}


async def test_cancelled_waiting_recovery_releases_its_place_without_changing_owner():
    store, operations = InMemoryLifecycleStore(), InMemoryExtensionOperationStore()
    record = await seed(store)
    first_runtime = PausingRuntime()
    first = service(store, operations, first_runtime)
    second = service(store, operations, FakeRuntime())
    running = asyncio.create_task(first.recover())
    try:
        await asyncio.wait_for(first_runtime.entered.wait(), 2)
        waiter = asyncio.create_task(second.recover())
        await settled_turns()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert (await store.get(record.manifest.id)).state is ExtensionState.STARTING
    finally:
        first_runtime.release.set()
        await running
    await asyncio.wait_for(second.recover(), 2)
    assert (await store.get(record.manifest.id)).state is ExtensionState.ENABLED


async def test_accepted_command_outlives_cancelled_requester():
    class CancelAfterClaimStore(InMemoryExtensionOperationStore):
        async def create(self, operation):
            await super().create(operation)
            requester.cancel()

    store, operations = InMemoryLifecycleStore(), CancelAfterClaimStore()
    record = await seed(store, ExtensionState.DISABLED)
    runtime = PausingRuntime()
    owner = service(store, operations, runtime)
    requester = asyncio.create_task(owner.begin_enable(record.manifest.id))
    try:
        with pytest.raises(asyncio.CancelledError):
            await requester
        await asyncio.wait_for(runtime.entered.wait(), 2)
        operation = next(iter(operations._operations.values()))
        assert operation.status is OperationState.RUNNING
        runtime.release.set()
        final = await owner.wait_operation(operation.id, timeout_seconds=2)
        assert final.status is OperationState.SUCCEEDED
        assert (await store.get(record.manifest.id)).state is ExtensionState.ENABLED
    finally:
        runtime.release.set()
        await asyncio.gather(*owner._tasks, return_exceptions=True)


async def test_upgrade_finishes_before_other_host_recovers_its_published_version():
    store, operations = InMemoryLifecycleStore(), InMemoryExtensionOperationStore()
    record = await seed(store)
    runtime = PausingRuntime()
    await FakeRuntime.start(runtime, record)
    owner = service(store, operations, runtime)
    newcomer_runtime = FakeRuntime()
    newcomer = service(store, operations, newcomer_runtime)
    candidate = replace(
        record,
        manifest=replace(record.manifest, version="0.2.0"),
        state=ExtensionState.INSTALLED_DISABLED,
    )
    operation = await owner._begin(
        record.manifest.id,
        "upgrade",
        lambda: owner._manager.upgrade(record.manifest.id, candidate),
        request_scope="test:upgrade",
    )
    recovery = None
    try:
        await asyncio.wait_for(runtime.entered.wait(), 2)
        recovery = asyncio.create_task(newcomer.recover())
        await settled_turns()
        assert not recovery.done()
        assert (await operations.get(operation.id)).status is OperationState.RUNNING
        runtime.release.set()
        final = await owner.wait_operation(operation.id, timeout_seconds=2)
        await asyncio.wait_for(recovery, 2)
        assert final.status is OperationState.SUCCEEDED
        saved = await store.get(record.manifest.id)
        assert (saved.state, saved.manifest.version) == (ExtensionState.ENABLED, "0.2.0")
        assert newcomer_runtime.started == ["0.2.0"]
    finally:
        runtime.release.set()
        await asyncio.gather(
            *(list(owner._tasks) + ([recovery] if recovery else [])), return_exceptions=True
        )


async def test_active_command_stays_reserved_until_shared_gate_has_released():
    class SlowUnlockStore(InMemoryLifecycleStore):
        def __init__(self):
            super().__init__()
            self.exiting = asyncio.Event()
            self.release_exit = asyncio.Event()

        @asynccontextmanager
        async def operation_lock(self):
            async with super().operation_lock():
                try:
                    yield
                finally:
                    self.exiting.set()
                    await self.release_exit.wait()

    store, operations = SlowUnlockStore(), InMemoryExtensionOperationStore()
    record = await seed(store, ExtensionState.DISABLED)
    owner = service(store, operations, FakeRuntime())
    operation = await owner.begin_enable(record.manifest.id)
    try:
        await asyncio.wait_for(store.exiting.wait(), 2)
        assert (await operations.get(operation.id)).status is OperationState.SUCCEEDED
        assert owner._active == {record.manifest.id}
    finally:
        store.release_exit.set()
        await asyncio.gather(*owner._tasks, return_exceptions=True)
    assert owner._active == set()


async def test_cancelled_recovery_closes_started_worker_before_releasing_gate():
    class PausingHealth(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()

        async def health(self, record):
            self.entered.set()
            await asyncio.Event().wait()

    store, operations = InMemoryLifecycleStore(), InMemoryExtensionOperationStore()
    record = await seed(store)
    runtime = PausingHealth()
    owner = service(store, operations, runtime)
    recovery = asyncio.create_task(owner.recover())
    await asyncio.wait_for(runtime.entered.wait(), 2)
    recovery.cancel()
    with pytest.raises(asyncio.CancelledError):
        await recovery
    assert runtime.active == {}
    newcomer = service(store, operations, FakeRuntime())
    await asyncio.wait_for(newcomer.recover(), 2)
    assert (await store.get(record.manifest.id)).state is ExtensionState.QUARANTINED
