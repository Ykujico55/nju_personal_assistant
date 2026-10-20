"""Standalone persistent Agent worker; can also be embedded in development only."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from datetime import UTC, datetime

from personal_assistant.bootstrap import Container, build_container
from personal_assistant.core.scheduling import DurableScheduler
from personal_assistant.domain import ApprovalState
from personal_assistant.infrastructure.operations.control import HostControl
from personal_assistant.runtime import build_runtime
from personal_assistant.settings import Settings


async def heartbeat(container: Container, stop: asyncio.Event) -> None:
    while not stop.is_set():
        old = await container.records.get("hosts", "agent")
        await container.records.put(
            "hosts",
            "agent",
            {"seen_at": datetime.now(UTC).isoformat()},
            expected_version=old["version"] if old else 0,
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=10)


async def loop(container: Container, stop: asyncio.Event, *, once: bool = False) -> None:
    scheduler = DurableScheduler(container.records, container.jobs)
    control = HostControl(container.settings.extension_root.parent / "control")
    heartbeat_task = asyncio.create_task(heartbeat(container, stop))
    try:
        while not stop.is_set():
            if control.frozen():
                if once:
                    return
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=1)
                continue
            await container.refresh_tool_registry()
            # Recover an approved queue notification lost at the API/process boundary.
            for execution in await container.records.list("executions"):
                value = execution["value"]
                if value.get("state") != "WAITING_APPROVAL" or not value.get("reference"):
                    continue
                approval = await container.approvals.get(value["reference"])
                if approval and approval.state is ApprovalState.APPROVED:
                    await container.jobs.enqueue(
                        kind="agent.approval",
                        payload={"task_id": execution["id"], "approval_id": approval.id},
                        idempotency_key="approved-action:" + approval.id,
                    )
            await scheduler.tick()
            worked = await build_runtime(container).work_once()
            if once:
                return
            if not worked:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=1)
    finally:
        stop.set()
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


async def serve(*, once: bool = False) -> None:
    settings = Settings.from_env()
    if settings.storage_backend != "postgres":
        raise ValueError("standalone worker requires persistent PostgreSQL; use embedded dev mode")
    container = build_container(settings)
    await container.storage.startup()
    try:
        await container.extension_supervisor.recover()
        await container.refresh_tool_registry()
        await loop(container, asyncio.Event(), once=once)
    finally:
        try:
            await container.aclose()
        finally:
            await container.storage.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="助手持久任务 Worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(serve(once=args.once))
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("WORKER_START_OR_RUNTIME_FAILED: 检查本机配置和数据库状态；未自动重放外部动作。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
