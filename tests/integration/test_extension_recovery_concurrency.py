"""Recovery coordination on a dedicated temporary PostgreSQL, never the original DB."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import unittest
from dataclasses import replace

from personal_assistant.core.extensions import (
    ExtensionRecord,
    ExtensionRegistry,
    ExtensionState,
    ManifestParser,
)
from personal_assistant.core.extensions.lifecycle import InstallCoordinator, LifecycleManager
from personal_assistant.core.extensions.operations import OperationState
from personal_assistant.core.extensions.supervision import ExtensionSupervisorService
from personal_assistant.infrastructure.database import (
    PostgresAdapterConfig,
    build_postgres_adapters,
)
from personal_assistant.infrastructure.database.operations import PostgresExtensionOperationStore
from tests.integration import test_postgres_f02 as base


class Runtime(base._Runtime):
    def __init__(self, *, block_start=False, block_drain=False):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block_start, self.block_drain = block_start, block_drain
        self.healthy = True

    async def start(self, record):
        if self.block_start:
            self.entered.set()
            await self.release.wait()
        await super().start(record)

    async def health(self, record):
        return self.healthy

    async def drain(self, record, deadline_epoch):
        if self.block_drain:
            self.entered.set()
            await self.release.wait()


def service(adapters, runtime):
    registry = ExtensionRegistry()
    return ExtensionSupervisorService(
        coordinator=InstallCoordinator(
            base._Stager(), base._Installer(), base._Verifier(), adapters.lifecycle_store
        ),
        manager=LifecycleManager(
            adapters.lifecycle_store, registry, runtime, base._Installer(), base._DataStore(), None
        ),
        registry=registry,
        store=adapters.lifecycle_store,
        operations=PostgresExtensionOperationStore(adapters.database),
        runtime=runtime,
    )


@unittest.skipUnless(base.BASE_URL, "dedicated temporary PostgreSQL required")
class ConcurrentRecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.PostgresF02Tests.asyncSetUp
    asyncTearDown = base.PostgresF02Tests.asyncTearDown
    _new_adapters = base.PostgresF02Tests._new_adapters

    async def prepare(self, state=ExtensionState.ENABLED):
        a, b = await self._new_adapters(), await self._new_adapters()
        record = ExtensionRecord(
            replace(ManifestParser().parse(base.EXAMPLE), id="test.concurrent"),
            "sha256:" + "0" * 64,
            state,
            install_path=str(self.tmp),
        )
        await a.lifecycle_store.save(record)
        return a, b, record

    async def finish(self, services, tasks, runtime):
        runtime.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for supervisor in services:
            await supervisor.stop_all()

    async def test_two_database_pools_do_not_quarantine_live_starting(self):
        a, b, record = await self.prepare()
        first_runtime, second_runtime = Runtime(block_start=True), Runtime()
        first, second = service(a, first_runtime), service(b, second_runtime)
        tasks = [asyncio.create_task(first.recover())]
        try:
            await asyncio.wait_for(first_runtime.entered.wait(), 3)
            tasks.append(asyncio.create_task(second.recover()))
            # Wait a bounded observation interval while the first runtime cannot finish.
            await asyncio.sleep(0.1)
            self.assertFalse(tasks[1].done(), "second host must wait for live recovery")
            self.assertEqual(
                ExtensionState.STARTING, (await b.lifecycle_store.get(record.manifest.id)).state
            )
            first_runtime.release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
            self.assertEqual(
                ExtensionState.ENABLED, (await a.lifecycle_store.get(record.manifest.id)).state
            )
            self.assertEqual(["0.1.0"], first_runtime.started)
            self.assertEqual(["0.1.0"], second_runtime.started)
        finally:
            await self.finish((first, second), tasks, first_runtime)

    async def test_live_disable_is_not_failed_or_undone_by_another_host_recovery(self):
        a, b, record = await self.prepare()
        runtime = Runtime(block_drain=True)
        first, second = service(a, runtime), service(b, Runtime())
        tasks = []
        try:
            operation = await first.begin_disable(
                record.manifest.id, idempotency_key="disable-live"
            )
            await asyncio.wait_for(runtime.entered.wait(), 3)
            tasks.append(asyncio.create_task(second.recover()))
            await asyncio.sleep(0.1)
            observed = await PostgresExtensionOperationStore(b.database).get(operation.id)
            self.assertEqual(OperationState.RUNNING, observed.status)
            runtime.release.set()
            await asyncio.wait_for(tasks[0], 5)
            observed = await PostgresExtensionOperationStore(b.database).get(operation.id)
            self.assertEqual(OperationState.SUCCEEDED, observed.status)
            self.assertEqual(
                ExtensionState.DISABLED, (await b.lifecycle_store.get(record.manifest.id)).state
            )
        finally:
            await self.finish((first, second), tasks, runtime)

    async def test_true_crash_residue_and_bad_health_still_quarantine(self):
        a, _, record = await self.prepare(ExtensionState.STARTING)
        runtime = Runtime()
        supervisor = service(a, runtime)
        try:
            await supervisor.recover()
            self.assertEqual(
                ExtensionState.QUARANTINED, (await a.lifecycle_store.get(record.manifest.id)).state
            )
            self.assertEqual([], runtime.started)
            await a.lifecycle_store.save(replace(record, state=ExtensionState.ENABLED))
            runtime.healthy = False
            await supervisor.recover()
            self.assertEqual(
                ExtensionState.QUARANTINED, (await a.lifecycle_store.get(record.manifest.id)).state
            )
        finally:
            await supervisor.stop_all()

    async def test_database_lock_waiter_cancellation_does_not_leak_the_gate(self):
        a, b, _ = await self.prepare()
        entered = asyncio.Event()

        async def wait_gate():
            async with b.lifecycle_store.operation_lock():
                entered.set()

        async with a.lifecycle_store.operation_lock():
            task = asyncio.create_task(wait_gate())
            await asyncio.sleep(0.1)
            self.assertFalse(entered.is_set())
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        async with asyncio.timeout(3):
            async with b.lifecycle_store.operation_lock():
                pass

    async def test_gate_does_not_exhaust_single_connection_application_pool(self):
        adapters = build_postgres_adapters(
            PostgresAdapterConfig(
                database_url=base._with_db(self._base, self._db_name),
                pool_min_size=1,
                pool_max_size=1,
            )
        )
        await adapters.startup()
        self._adapters.append(adapters)
        record = ExtensionRecord(
            replace(ManifestParser().parse(base.EXAMPLE), id="test.single.pool"),
            "sha256:" + "0" * 64,
            ExtensionState.ENABLED,
            install_path=str(self.tmp),
        )
        await adapters.lifecycle_store.save(record)
        baseline_connections = tuple(adapters.database._extra)
        supervisor = service(adapters, Runtime())
        try:
            async with asyncio.timeout(5):
                await supervisor.recover()
            self.assertEqual(
                ExtensionState.ENABLED,
                (await adapters.lifecycle_store.get(record.manifest.id)).state,
            )
            self.assertEqual(baseline_connections, tuple(adapters.database._extra))
        finally:
            await supervisor.stop_all()

    async def test_killed_other_process_releases_postgres_lifecycle_gate(self):
        adapters, _, _ = await self.prepare()
        child_code = """
import asyncio, os
from personal_assistant.infrastructure.database import (
    PostgresAdapterConfig, build_postgres_adapters,
)
async def main():
    a=build_postgres_adapters(PostgresAdapterConfig(database_url=os.environ['PA_COORDINATION_DSN']))
    await a.database.connect()
    try:
        async with a.lifecycle_store.operation_lock():
            print('GATE_HELD', flush=True)
            await asyncio.Event().wait()
    finally:
        await a.close()
asyncio.run(main())
"""
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-Xutf8",
            "-B",
            "-c",
            child_code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PA_COORDINATION_DSN": base._with_db(self._base, self._db_name)},
        )
        task = None
        entered = asyncio.Event()

        async def take_gate():
            async with adapters.lifecycle_store.operation_lock():
                entered.set()

        try:
            ready = await asyncio.wait_for(child.stdout.readline(), 10)
            self.assertEqual(b"GATE_HELD\n", ready.replace(b"\r\n", b"\n"))
            task = asyncio.create_task(take_gate())
            await asyncio.sleep(0.1)
            self.assertFalse(entered.is_set())
            child.terminate()
            await asyncio.wait_for(child.wait(), 5)
            await asyncio.wait_for(task, 5)
            self.assertTrue(entered.is_set())
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await child.communicate()
