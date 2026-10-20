"""Creation cleanup keeps driver resources and session admission bounded."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from personal_assistant.infrastructure.browser import companion as module
from personal_assistant.infrastructure.browser.companion import CompanionError, DesktopCompanion


class ResourceDriver:
    def __init__(self, *, release=None, fail=False):
        self.release, self.fail = release, fail
        self.entered = asyncio.Event()
        self.alive = False

    async def start(self):
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        self.alive = True
        if self.fail:
            raise RuntimeError("synthetic partial start failure")

    async def close(self):
        self.alive = False


class CreationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_start_failure_closes_allocated_resource(self):
        driver = ResourceDriver(fail=True)
        companion = DesktopCompanion(driver_factory=lambda **_: driver)
        try:
            with self.assertRaisesRegex(RuntimeError, "partial start"):
                await companion.create_session(
                    session_id="failed",
                    purpose="fixture",
                    allowed_origins=("https://example.test",),
                    task_id="task",
                    extension_id="fixture",
                    token=companion.root_capability,
                )
            self.assertFalse(driver.alive)
            self.assertEqual(
                0, (await companion.diagnostics(token=companion.root_capability))["session_count"]
            )
        finally:
            await companion.shutdown()
            await driver.close()

    async def test_revoked_pending_start_keeps_capacity_until_it_finishes(self):
        release = asyncio.Event()
        drivers = []

        def factory(**_):
            driver = ResourceDriver(release=release if not drivers else None)
            drivers.append(driver)
            return driver

        companion = DesktopCompanion(driver_factory=factory, max_sessions=1)

        async def create(session_id):
            return await companion.create_session(
                session_id=session_id,
                purpose="fixture",
                allowed_origins=("https://example.test",),
                task_id="task",
                extension_id="fixture",
                token=companion.root_capability,
            )

        pending = asyncio.create_task(create("pending"))
        try:
            while not drivers:
                await asyncio.sleep(0)
            await drivers[0].entered.wait()
            await companion.revoke(session_id="pending", token=companion.root_capability)
            with self.assertRaises(CompanionError) as error:
                await create("second")
            self.assertEqual("SESSION_LIMIT", error.exception.code)
            self.assertEqual(1, len(drivers))
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await companion.shutdown()
            for driver in drivers:
                await driver.close()

    async def test_single_use_identity_history_is_bounded_without_eviction(self):
        companion = DesktopCompanion(driver_factory=lambda **_: ResourceDriver())
        try:
            with patch.object(module, "MAX_SESSION_IDENTITIES", 2, create=True):
                for session_id in ("first", "second"):
                    await companion.revoke(session_id=session_id, token=companion.root_capability)
                with self.assertRaises(CompanionError) as error:
                    await companion.revoke(session_id="third", token=companion.root_capability)
                self.assertEqual("SESSION_HISTORY_LIMIT", error.exception.code)
                with self.assertRaises(CompanionError) as error:
                    await companion.create_session(
                        session_id="first",
                        purpose="fixture",
                        allowed_origins=("https://example.test",),
                        task_id="task",
                        extension_id="fixture",
                        token=companion.root_capability,
                    )
                self.assertEqual("SESSION_REVOKED", error.exception.code)
        finally:
            await companion.shutdown()


class CleanupFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_root_close_keeps_ttl_for_automatic_cleanup(self):
        finished = asyncio.Event()

        class RetryDriver(ResourceDriver):
            close_calls = 0

            async def close(self):
                self.close_calls += 1
                if self.close_calls == 1:
                    raise RuntimeError("synthetic close failure")
                await super().close()
                finished.set()

        driver = RetryDriver()
        companion = DesktopCompanion(driver_factory=lambda **_: driver, ttl_seconds=1)
        try:
            await companion.create_session(
                session_id="retry",
                purpose="fixture",
                allowed_origins=("https://example.test",),
                task_id="task",
                extension_id="fixture",
                token=companion.root_capability,
            )
            with self.assertRaises(CompanionError) as error:
                await companion.revoke(session_id="retry", token=companion.root_capability)
            self.assertEqual("SESSION_CLEANUP_FAILED", error.exception.code)
            self.assertTrue(driver.alive)
            await asyncio.wait_for(finished.wait(), 2)
            self.assertFalse(driver.alive)
            self.assertEqual(
                0, (await companion.diagnostics(token=companion.root_capability))["session_count"]
            )
        finally:
            await companion.shutdown()
            await driver.close()

    async def test_shutdown_attempts_other_sessions_when_one_close_fails(self):
        class RejectCloseDriver(ResourceDriver):
            reject = True

            async def close(self):
                if self.reject:
                    raise RuntimeError("synthetic close failure")
                await super().close()

        first, second = RejectCloseDriver(), ResourceDriver()
        drivers = iter((first, second))
        companion = DesktopCompanion(driver_factory=lambda **_: next(drivers))
        try:
            for session_id in ("first", "second"):
                await companion.create_session(
                    session_id=session_id,
                    purpose="fixture",
                    allowed_origins=("https://example.test",),
                    task_id="task",
                    extension_id="fixture",
                    token=companion.root_capability,
                )
            with self.assertRaises(CompanionError):
                await companion.shutdown()
            self.assertFalse(second.alive)
        finally:
            first.reject = False
            await companion.shutdown()
            await first.close()
            await second.close()


class DiagnosticsExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostics_tolerates_expiry_during_driver_read(self):
        closed = asyncio.Event()

        class DiagnosticsDriver(ResourceDriver):
            async def close(self):
                await super().close()
                closed.set()

            async def diagnostics(self):
                await closed.wait()
                return {"browser_alive": self.alive}

        driver = DiagnosticsDriver()
        companion = DesktopCompanion(driver_factory=lambda **_: driver, ttl_seconds=1)
        try:
            await companion.create_session(
                session_id="expiry",
                purpose="fixture",
                allowed_origins=("https://example.test",),
                task_id="task",
                extension_id="fixture",
                token=companion.root_capability,
            )
            result = await asyncio.wait_for(
                companion.diagnostics(token=companion.root_capability), 2
            )
            self.assertFalse(result["sessions"][0]["driver"]["browser_alive"])
            self.assertFalse(driver.alive)
        finally:
            await companion.shutdown()
            await driver.close()
