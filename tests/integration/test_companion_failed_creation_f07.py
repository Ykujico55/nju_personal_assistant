"""Failed Companion creation must not leave an unowned browser session.

Uses the real loopback HTTP application and client. The driver is a synthetic
resource with a controlled startup gate; no browser or external site is used.
"""

from __future__ import annotations

import asyncio
import socket
import unittest
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import Any
from unittest.mock import patch

import httpx
import uvicorn

from personal_assistant.core.browser import BrowserUnavailableError
from personal_assistant.infrastructure.browser.companion import DesktopCompanion
from personal_assistant.infrastructure.browser.companion_app import create_companion_app
from personal_assistant.infrastructure.browser.companion_client import LoopbackCompanionClient


class DelayedResourceDriver:
    """A successful late start can allocate after an earlier close call."""

    def __init__(self, release: asyncio.Event) -> None:
        self.release = release
        self.entered = asyncio.Event()
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.alive = False
        self.close_calls = 0

    async def start(self) -> None:
        self.entered.set()
        await self.release.wait()
        self.alive = True
        self.started.set()

    async def close(self) -> None:
        self.close_calls += 1
        self.alive = False
        self.closed.set()

    async def diagnostics(self) -> Mapping[str, Any]:
        return {"browser_alive": self.alive}


class CompanionFailedCreationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.release = asyncio.Event()
        self.drivers: list[DelayedResourceDriver] = []
        self.requests: list[asyncio.Task[Any]] = []

        def factory(**_: Any) -> DelayedResourceDriver:
            driver = DelayedResourceDriver(self.release)
            self.drivers.append(driver)
            return driver

        self.companion = DesktopCompanion(driver_factory=factory, ttl_seconds=30)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.listener.setblocking(False)
        port = int(self.listener.getsockname()[1])
        self.url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(
            uvicorn.Config(
                create_companion_app(self.companion),
                log_config=None,
                log_level="critical",
                access_log=False,
                lifespan="off",
                timeout_graceful_shutdown=2,
            )
        )
        self.serving = asyncio.create_task(self.server.serve(sockets=[self.listener]))
        async with asyncio.timeout(5):
            while not self.server.started:
                await asyncio.sleep(0.01)
        self.http = httpx.AsyncClient(base_url=self.url, trust_env=False, timeout=5)
        self.client = LoopbackCompanionClient(
            base_url=self.url,
            root_capability=self.companion.root_capability,
            command_timeout_seconds=5,
        )

    async def asyncTearDown(self) -> None:
        self.release.set()
        if self.requests:
            await asyncio.gather(*self.requests, return_exceptions=True)
        await self.companion.shutdown()
        try:
            # Preserve this product-cleanup assertion, including its failure.
            self.assertFalse(any(driver.alive for driver in self.drivers))
        finally:
            # Emergency cleanup runs only after the assertion; it cannot turn
            # a failed product assertion into a passing test.
            for driver in self.drivers:
                await driver.close()
            await self.client.aclose()
            await self.http.aclose()
            self.server.should_exit = True
            await asyncio.wait_for(self.serving, timeout=8)
            self.listener.close()

    def headers(self, token: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token or self.companion.root_capability}"}

    @staticmethod
    def payload(session_id: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "purpose": "local failed-creation resource check",
            "allowed_origins": ["https://fixture.invalid"],
            "task_id": "task-failed-creation",
            "extension_id": "fixture.synthetic",
        }

    @contextmanager
    def delayed_client_request(self, path: str) -> Iterator[None]:
        """Normal loopback requests may legitimately take more than 150ms."""
        http = self.client._http()
        request = http.request

        async def delayed(method: str, url: str, **kwargs: Any) -> httpx.Response:
            if method == "POST" and url == path:
                await asyncio.sleep(0.3)
            return await request(method, url, **kwargs)

        with patch.object(http, "request", new=delayed):
            yield

    @asynccontextmanager
    async def tracked_creation(
        self, session_id: str
    ) -> AsyncIterator[asyncio.Task[Mapping[str, Any]]]:
        creating = asyncio.create_task(self.client.create_session(**self.payload(session_id)))
        self.requests.append(creating)
        try:
            yield creating
        finally:
            self.release.set()
            # Retrieve failures even when the boundary assertion fails first.
            await asyncio.gather(creating, return_exceptions=True)

    async def wait_for_start(self, creating: asyncio.Task[Any]) -> None:
        async with asyncio.timeout(3):
            while True:
                if creating.done():
                    await creating
                    self.fail("creation finished before the expected pending startup")
                if self.drivers and self.drivers[0].entered.is_set():
                    return
                await asyncio.sleep(0.01)

    @contextmanager
    def creation_response_timeout(self) -> Iterator[None]:
        """Inject a read timeout only after the real server starts the driver."""
        http = self.client._http()
        request = http.request

        async def fail_create(method: str, url: str, **kwargs: Any) -> httpx.Response:
            if method != "POST" or url != "/v1/sessions":
                return await request(method, url, **kwargs)
            sending = asyncio.create_task(request(method, url, **kwargs))
            self.requests.append(sending)
            try:
                await self.wait_for_start(sending)
                try:
                    async with asyncio.timeout(0.15):
                        return await sending
                except TimeoutError:
                    raise httpx.ReadTimeout("injected create response timeout") from None
            finally:
                if not sending.done():
                    sending.cancel()
                await asyncio.gather(sending, return_exceptions=True)

        with patch.object(http, "request", new=fail_create):
            yield

    async def start_raw_creation(self, session_id: str) -> asyncio.Task[httpx.Response]:
        request = asyncio.create_task(
            self.http.post("/v1/sessions", headers=self.headers(), json=self.payload(session_id))
        )
        self.requests.append(request)
        await self.wait_for_start(request)
        return request

    async def assert_reclaimed(self) -> None:
        async with asyncio.timeout(2):
            while any(driver.alive for driver in self.drivers):
                await asyncio.sleep(0.01)
        self.assertTrue(self.drivers[0].closed.is_set())
        self.assertFalse(self.drivers[0].alive)
        result = await self.http.get("/v1/diagnostics", headers=self.headers())
        self.assertEqual(200, result.status_code)
        self.assertEqual(0, result.json()["session_count"])

    async def test_timed_out_creation_is_reclaimed_even_when_start_finishes_later(self) -> None:
        with self.creation_response_timeout():
            async with self.tracked_creation("timed-out") as create:
                with self.assertRaises(BrowserUnavailableError):
                    await create
                self.assertTrue(self.drivers[0].entered.is_set())
                self.release.set()
                await asyncio.wait_for(self.drivers[0].started.wait(), timeout=2)
                await self.assert_reclaimed()
                self.assertNotIn("timed-out", self.client._session_tokens)

    async def test_cancelled_create_reclaims_late_start(self) -> None:
        with self.delayed_client_request("/v1/sessions"):
            async with self.tracked_creation("cancelled-create") as create:
                await self.wait_for_start(create)
                self.assertFalse(create.done())
                create.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await create
                self.release.set()
                await asyncio.wait_for(self.drivers[0].started.wait(), timeout=2)
                await self.assert_reclaimed()
                self.assertNotIn("cancelled-create", self.client._session_tokens)

    async def test_cancel_without_returned_capability_uses_host_authority(self) -> None:
        creating = await self.start_raw_creation("no-returned-capability")
        with self.delayed_client_request("/v1/revoke"):
            await self.client.cancel_session("no-returned-capability")
        self.release.set()
        response = await creating
        self.assertGreaterEqual(response.status_code, 400)
        self.assertNotIn("capability", response.json())
        await self.assert_reclaimed()

    async def test_wait_for_start_surfaces_a_finished_creation_failure(self) -> None:
        async def failed_creation() -> None:
            raise BrowserUnavailableError("fixture creation failed before registration")

        creating = asyncio.create_task(failed_creation())
        self.requests.append(creating)
        try:
            with self.assertRaisesRegex(BrowserUnavailableError, "before registration"):
                await self.wait_for_start(creating)
        finally:
            await asyncio.gather(creating, return_exceptions=True)

    async def test_root_revoke_while_start_pending_cannot_publish_a_late_capability(self) -> None:
        creating = await self.start_raw_creation("revoke-during-start")
        revoked = await self.http.post(
            "/v1/revoke", headers=self.headers(), json={"session_id": "revoke-during-start"}
        )
        self.assertEqual(200, revoked.status_code)
        repeated = await self.http.post(
            "/v1/revoke", headers=self.headers(), json={"session_id": "revoke-during-start"}
        )
        self.assertEqual(200, repeated.status_code)
        self.release.set()
        response = await creating
        self.assertGreaterEqual(response.status_code, 400)
        self.assertNotIn("capability", response.json())
        await self.assert_reclaimed()
        repeated = await self.http.post(
            "/v1/revoke", headers=self.headers(), json={"session_id": "revoke-during-start"}
        )
        self.assertEqual(200, repeated.status_code)
        await self.assert_reclaimed()

    async def test_root_revoke_before_registration_rejects_delayed_create(self) -> None:
        revoked = await self.http.post(
            "/v1/revoke", headers=self.headers(), json={"session_id": "revoked-first"}
        )
        self.assertEqual(200, revoked.status_code)
        self.release.set()
        response = await self.http.post(
            "/v1/sessions", headers=self.headers(), json=self.payload("revoked-first")
        )
        self.assertGreaterEqual(response.status_code, 400)
        self.assertNotIn("capability", response.json())
        self.assertEqual([], self.drivers)

    async def test_idle_ttl_closes_without_a_following_create_or_status_request(self) -> None:
        self.release.set()
        # Only this test exercises TTL expiry. Normal revoke/cancel tests must
        # not be cleaned up by a one-second expiry before their target action.
        with patch.object(self.companion, "_ttl", 1):
            response = await self.http.post(
                "/v1/sessions", headers=self.headers(), json=self.payload("idle-expiry")
            )
        self.assertEqual(200, response.status_code)
        self.assertTrue(self.drivers[0].alive)
        # Waiting is deliberately the only action: a status call or a second
        # create would hide the former passive-only expiry behavior.
        await asyncio.wait_for(self.drivers[0].closed.wait(), timeout=2.5)
        await self.assert_reclaimed()

    async def test_non_host_cannot_revoke_a_pending_creation(self) -> None:
        creating = await self.start_raw_creation("authorization-boundary")
        denied = await self.http.post(
            "/v1/revoke",
            headers=self.headers("not-the-root"),
            json={"session_id": "authorization-boundary"},
        )
        self.assertEqual(401, denied.status_code)
        self.assertEqual(0, self.drivers[0].close_calls)
        self.release.set()
        response = await creating
        self.assertEqual(200, response.status_code)
        capability = response.json()["capability"]
        status = await self.http.get(
            "/v1/sessions/authorization-boundary", headers=self.headers(capability)
        )
        self.assertEqual(200, status.status_code)
        self.assertTrue(self.drivers[0].alive)
        revoked = await self.http.post(
            "/v1/revoke",
            headers=self.headers(),
            json={"session_id": "authorization-boundary"},
        )
        self.assertEqual(200, revoked.status_code)
        await self.assert_reclaimed()
