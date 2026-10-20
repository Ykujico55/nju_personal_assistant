"""Cancellation during cleanup cannot strand a revoked browser resource."""

from __future__ import annotations

import asyncio
import unittest

from personal_assistant.infrastructure.browser.companion import DesktopCompanion


class PausingCloseDriver:
    def __init__(self, *, pause_start: bool = False) -> None:
        self.pause_start = pause_start
        self.started = asyncio.Event()
        self.closing = asyncio.Event()
        self.release_close = asyncio.Event()
        self.alive = False

    async def start(self) -> None:
        self.alive = True
        self.started.set()
        if self.pause_start:
            await asyncio.Event().wait()

    async def close(self) -> None:
        self.closing.set()
        await self.release_close.wait()
        self.alive = False


class CleanupCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, cancel_creation: bool) -> None:
        driver = PausingCloseDriver(pause_start=cancel_creation)
        companion = DesktopCompanion(driver_factory=lambda **_: driver)
        creation = asyncio.create_task(
            companion.create_session(
                session_id="fixture-cleanup-cancellation",
                purpose="synthetic lifecycle check",
                allowed_origins=("https://example.test",),
                task_id="fixture-task",
                extension_id="fixture",
                token=companion.root_capability,
            )
        )
        operation = creation
        try:
            await asyncio.wait_for(driver.started.wait(), timeout=1)
            if cancel_creation:
                creation.cancel()
            else:
                await creation
                operation = asyncio.create_task(
                    companion.revoke(
                        session_id="fixture-cleanup-cancellation",
                        token=companion.root_capability,
                    )
                )
            await asyncio.wait_for(driver.closing.wait(), timeout=1)
            operation.cancel()
            asyncio.get_running_loop().call_later(0.05, driver.release_close.set)
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(operation, timeout=1)
            self.assertFalse(driver.alive, "cancellation stranded the browser resource")
            diagnostics = await companion.diagnostics(token=companion.root_capability)
            self.assertEqual(0, diagnostics["session_count"])
        finally:
            driver.release_close.set()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            await companion.shutdown()
            await driver.close()

    async def test_cancelled_root_revocation_finishes_resource_cleanup(self) -> None:
        await self.exercise(cancel_creation=False)

    async def test_second_creation_cancel_does_not_abort_failed_start_cleanup(self) -> None:
        await self.exercise(cancel_creation=True)
