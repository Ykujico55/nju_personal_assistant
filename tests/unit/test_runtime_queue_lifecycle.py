from __future__ import annotations

import asyncio
import unittest
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from personal_assistant.core.agent.safety_fuse import EmergencyFuse, FuseThresholds
from personal_assistant.core.jobs.queue import JobState
from personal_assistant.domain import RiskLevel, TaskRun, TaskState
from personal_assistant.runtime import build_runtime
from tests.unit import test_runtime_delivery as fixtures


class RuntimeQueueLifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.RuntimeDeliveryTests.asyncSetUp

    async def _stop_planning(self, *, freeze=False, expire=False, late=False):
        task = await self.container.tasks.create(
            objective="blocked planner", idempotency_key="first"
        )
        runtime = build_runtime(self.container)
        started, cancelled = asyncio.Event(), asyncio.Event()
        frozen = False
        runtime.paused_probe = lambda: frozen

        async def propose(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if late:
                    return {"kind": "COMPLETE", "message": "late result must be discarded"}
                raise
            finally:
                cancelled.set()

        runtime.planner.propose = AsyncMock(side_effect=propose)
        working = asyncio.create_task(runtime.work_once())
        try:
            await asyncio.wait_for(started.wait(), 2)
            self.assertEqual(task.objective, runtime.planner.propose.await_args.kwargs["objective"])
            # Claim the intended first task before enqueuing another. On Windows,
            # equal UTC timestamps use random Job IDs as the queue tie-breaker.
            next_task = await self.container.tasks.create(
                objective="next task", idempotency_key="second"
            )
            if freeze:
                frozen = True
            elif expire:
                runtime.fuse = EmergencyFuse(
                    FuseThresholds(active_duration=timedelta(microseconds=1))
                )
            else:
                current = await self.container.tasks.get(task.id)
                await self.container.tasks.cancel(
                    task_id=task.id,
                    expected_version=current.version,
                    actor="owner",
                    idempotency_key="cancel",
                )
            await asyncio.wait_for(asyncio.shield(working), 2)
            self.assertTrue(cancelled.is_set(), "planner coroutine must be reclaimed")
            expected = TaskState.PAUSED_SAFETY if freeze or expire else TaskState.CANCELLED
            self.assertEqual(expected, (await self.container.tasks.get(task.id)).state)
            self.assertEqual(
                expected, (await self.container.run_repository.get("run_" + task.id)).state
            )
            execution = await self.container.records.get("executions", task.id)
            self.assertEqual(expected.value, execution["value"]["state"])
            if expire:
                self.assertEqual("ACTIVE_RUN_DURATION", execution["value"]["reason"])
            self.assertFalse(
                any(
                    "late result" in m.content for m in await self.container.tasks.messages(task.id)
                )
            )
            frozen = False
            runtime.fuse = EmergencyFuse()
            runtime.planner.propose = AsyncMock(
                return_value={"kind": "COMPLETE", "message": "done"}
            )
            self.assertTrue(await runtime.work_once())
            self.assertEqual(
                TaskState.SUCCEEDED, (await self.container.tasks.get(next_task.id)).state
            )
        finally:
            if not working.done():
                working.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await working

    async def test_cancel_during_model_planning_releases_worker_and_records_cancelled(self):
        await self._stop_planning()

    async def test_cancel_discards_a_late_successful_planner_result(self):
        await self._stop_planning(late=True)

    async def test_freeze_during_planning_stops_without_discarding_task(self):
        await self._stop_planning(freeze=True)

    async def test_active_duration_fuse_interrupts_a_pending_planner(self):
        await self._stop_planning(expire=True)

    async def test_cancel_during_preview_never_dispatches_external_action(self):
        descriptor = replace(
            fixtures.DecisionDeliveryTests().descriptor(), risk=RiskLevel.EXTERNAL_WRITE
        )
        self.container.tool_registry.publish((descriptor,))
        task = await self.container.tasks.create(
            objective="preview fixture", idempotency_key="preview"
        )
        runtime = build_runtime(self.container)
        runtime.planner.propose = AsyncMock(
            return_value={
                "kind": "CALL_TOOL",
                "tool_id": descriptor.id,
                "tool_version": descriptor.version,
                "arguments": {},
            }
        )
        started, stopped = asyncio.Event(), asyncio.Event()

        async def prepare(call):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        runtime.prepare = prepare
        working = None
        with patch.object(runtime.tools, "invoke", new=AsyncMock()) as invoke:
            try:
                working = asyncio.create_task(runtime.work_once())
                await asyncio.wait_for(started.wait(), 2)
                current = await self.container.tasks.get(task.id)
                await self.container.tasks.cancel(
                    task_id=task.id,
                    expected_version=current.version,
                    actor="owner",
                    idempotency_key="cancel-preview",
                )
                await asyncio.wait_for(asyncio.shield(working), 2)
                self.assertTrue(stopped.is_set())
                invoke.assert_not_awaited()
                self.assertEqual(
                    TaskState.CANCELLED, (await runtime.runs.get("run_" + task.id)).state
                )
                self.assertNotEqual(
                    "dispatching", (await runtime.checkpoints.load("run_" + task.id)).get("phase")
                )
            finally:
                if working is not None:
                    if not working.done():
                        working.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await working

    async def test_missing_task_job_is_failed_without_crashing_worker(self):
        job = await self.container.jobs.enqueue(
            kind="agent.start", payload={"task_id": "missing"}, idempotency_key="missing"
        )
        runtime = build_runtime(self.container)
        self.assertTrue(await runtime.work_once())
        self.assertEqual(JobState.FAILED, (await self.container.jobs.get(job.id)).state)
        task = await self.container.tasks.create(objective="next", idempotency_key="next")
        runtime.planner.propose = AsyncMock(return_value={"kind": "COMPLETE", "message": "done"})
        self.assertTrue(await runtime.work_once())
        self.assertEqual(TaskState.SUCCEEDED, (await self.container.tasks.get(task.id)).state)

    async def test_failure_before_run_creation_does_not_leave_task_queued(self):
        task = await self.container.tasks.create(
            objective="cannot start", idempotency_key="start-fail"
        )
        runtime = build_runtime(self.container)
        with patch.object(
            runtime.runs, "add", new=AsyncMock(side_effect=RuntimeError("synthetic failure"))
        ):
            self.assertTrue(await runtime.work_once())
        self.assertEqual(TaskState.FAILED, (await self.container.tasks.get(task.id)).state)
        self.assertFalse(await runtime.work_once())

    async def test_startup_failure_preserves_orphan_unknown_checkpoint(self):
        task = await self.container.tasks.create(
            objective="orphan fixture", idempotency_key="orphan"
        )
        runtime = build_runtime(self.container)
        run = TaskRun(id="run_" + task.id, task_id=task.id, objective=task.objective)
        snapshot = {"unresolved_external": True, "phase": "dispatching"}
        await runtime.checkpoints.save(run, snapshot)
        before = await runtime.checkpoints.load(run.id)
        with patch.object(
            runtime.runs, "add", new=AsyncMock(side_effect=RuntimeError("startup failure"))
        ):
            self.assertTrue(await runtime.work_once())
        self.assertEqual(
            TaskState.WAITING_RECONCILIATION, (await self.container.tasks.get(task.id)).state
        )
        self.assertEqual(before, await runtime.checkpoints.load(run.id))

    async def test_cancel_in_dispatch_checkpoint_window_finishes_run_without_invoking(self):
        descriptor = fixtures.DecisionDeliveryTests().descriptor()
        self.container.tool_registry.publish((descriptor,))
        task = await self.container.tasks.create(
            objective="dispatch fixture", idempotency_key="dispatch"
        )
        runtime = build_runtime(self.container)
        runtime.planner.propose = AsyncMock(
            return_value={
                "kind": "CALL_TOOL",
                "tool_id": descriptor.id,
                "tool_version": descriptor.version,
                "arguments": {},
            }
        )
        save = runtime.checkpoints.save

        async def cancel_after_checkpoint(run, snapshot):
            await save(run, snapshot)
            current = await self.container.tasks.get(task.id)
            if snapshot.get("phase") == "dispatching" and current.state is not TaskState.CANCELLED:
                await self.container.tasks.cancel(
                    task_id=task.id,
                    expected_version=current.version,
                    actor="owner",
                    idempotency_key="cancel-before-invoke",
                )

        with (
            patch.object(runtime.checkpoints, "save", side_effect=cancel_after_checkpoint),
            patch.object(runtime.tools, "invoke", new=AsyncMock()) as invoke,
        ):
            self.assertTrue(await runtime.work_once())
            invoke.assert_not_awaited()
        self.assertEqual(TaskState.CANCELLED, (await runtime.runs.get("run_" + task.id)).state)
        execution = await self.container.records.get("executions", task.id)
        self.assertEqual("CANCELLED", execution["value"]["state"])

    async def test_cancel_targets_claimed_task_when_queue_timestamps_tie(self):
        fixed_time = datetime.now(UTC)
        with (
            patch(
                "personal_assistant.infrastructure.memory.job_queue._utcnow",
                return_value=fixed_time,
            ),
            patch(
                "personal_assistant.infrastructure.memory.job_queue.uuid4",
                side_effect=[SimpleNamespace(hex="z"), SimpleNamespace(hex="a")],
            ),
        ):
            await self._stop_planning()
