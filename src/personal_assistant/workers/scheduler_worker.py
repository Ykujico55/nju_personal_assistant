"""Standalone durable scheduler. It only enqueues read-only event collection."""

from __future__ import annotations

import asyncio

from personal_assistant.bootstrap import build_container
from personal_assistant.core.scheduling import DurableScheduler
from personal_assistant.settings import Settings


async def serve() -> None:
    settings = Settings.from_env()
    if settings.storage_backend != "postgres":
        raise ValueError("scheduler requires persistent PostgreSQL")
    container = build_container(settings)
    await container.storage.startup()
    try:
        scheduler = DurableScheduler(container.records, container.jobs)
        while True:
            await scheduler.tick()
            await asyncio.sleep(10)
    finally:
        try:
            await container.aclose()
        finally:
            await container.storage.close()


def main() -> int:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("SCHEDULER_FAILED: 检查数据库和定时任务配置；外部动作不会补放。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
