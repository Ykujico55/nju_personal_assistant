"""F07 audit: a session capability must never leak through an exception chain."""

from __future__ import annotations

import asyncio
import traceback
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from personal_assistant.core.browser import BrowserPolicyError, BrowserUnavailableError
from personal_assistant.infrastructure.browser.companion_client import LoopbackCompanionClient

CAPABILITY = "cap-secret-token-do-not-leak"
CLIENT_FILE = Path(
    "src/personal_assistant/infrastructure/browser/companion_client.py"
).name


def _handler_factory(behaviour: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if behaviour == "timeout":
            raise httpx.ReadTimeout("read timed out", request=request)
        if behaviour == "transport":
            raise httpx.ConnectError("connection refused", request=request)
        if behaviour == "server":
            return httpx.Response(503, json={"error": {"code": "BOOM", "message": "boom"}})
        if behaviour == "capability":
            return httpx.Response(
                403,
                json={
                    "error": {
                        "code": "CAPABILITY_DENIED",
                        "message": "the capability was rejected",
                    }
                },
            )
        if behaviour == "policy":
            return httpx.Response(
                409,
                json={
                    "error": {
                        "code": "SUBMIT_TARGET_REQUIRED",
                        "message": "a bound submission target is required",
                    }
                },
            )
        if behaviour == "invalid-json":
            return httpx.Response(200, text="<html>not json</html>")
        raise AssertionError(f"unexpected behaviour {behaviour}")

    return handler


class CompanionUrlTests(unittest.TestCase):
    def test_ipv6_loopback_is_bracketed(self) -> None:
        client = LoopbackCompanionClient(
            base_url="http://[::1]:8765", root_capability="cap"
        )
        self.assertEqual("http://[::1]:8765", client._base_url)

    def test_ipv4_and_localhost_are_unchanged(self) -> None:
        for raw in ("http://127.0.0.1:8765", "http://localhost:8765"):
            with self.subTest(raw=raw):
                client = LoopbackCompanionClient(base_url=raw, root_capability="cap")
                self.assertEqual(raw, client._base_url)


class CapabilityRedactionTests(unittest.IsolatedAsyncioTestCase):
    """Six independent failure paths, each checked for capability leakage."""

    def make_client(self, behaviour: str) -> LoopbackCompanionClient:
        client = LoopbackCompanionClient(
            base_url="http://127.0.0.1:1", root_capability=CAPABILITY
        )
        client._client = httpx.AsyncClient(
            base_url="http://127.0.0.1:1",
            transport=httpx.MockTransport(_handler_factory(behaviour)),
        )
        return client

    def _collect(self, value: Any, seen: set[int], depth: int = 0) -> list[str]:
        """Recursively collect textual representations, including nested
        httpx Request/Response objects and their headers."""

        if depth > 4 or len(seen) > 200:
            return []
        blobs = [repr(value)]
        if id(value) in seen:
            return blobs
        seen.add(id(value))
        if isinstance(value, Mapping):
            for key, item in value.items():
                blobs.extend(self._collect(key, seen, depth + 1))
                blobs.extend(self._collect(item, seen, depth + 1))
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                blobs.extend(self._collect(item, seen, depth + 1))
        else:
            for name in ("headers", "request", "response", "_request", "_response"):
                item = getattr(value, name, None)
                if item is not None:
                    blobs.extend(self._collect(item, seen, depth + 1))
            instance_dict = getattr(value, "__dict__", None)
            if isinstance(instance_dict, dict):
                for item in instance_dict.values():
                    blobs.extend(self._collect(item, seen, depth + 1))
        return blobs

    def assert_redacted(self, exc: BaseException) -> None:
        blobs: list[str] = [
            repr(exc),
            repr(exc.args),
            repr(exc.__cause__),
            repr(exc.__context__),
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        ]
        frames = 0
        traceback_obj = exc.__traceback__
        while traceback_obj is not None:
            frame = traceback_obj.tb_frame
            if Path(frame.f_code.co_filename).name == CLIENT_FILE:
                frames += 1
                # ``self`` legitimately owns the root capability; only the
                # error-path locals (token, headers, body, response) matter.
                locals_without_self = {
                    key: value
                    for key, value in frame.f_locals.items()
                    if key != "self"
                }
                blobs.extend(self._collect(locals_without_self, set()))
            traceback_obj = traceback_obj.tb_next
        self.assertGreaterEqual(frames, 1, "no companion_client frame in the traceback")
        combined = "\n".join(blobs)
        self.assertNotIn(CAPABILITY, combined)
        self.assertNotIn("Bearer", combined)

    async def expect_error(
        self, coroutine: Any, exc_type: type[BaseException]
    ) -> BaseException:
        """Return the live exception (unittest.assertRaises strips the traceback)."""

        try:
            await coroutine
        except exc_type as exc:
            return exc
        raise AssertionError(f"{exc_type.__name__} was not raised")

    async def test_timeout_does_not_leak_the_capability(self) -> None:
        client = self.make_client("timeout")
        try:
            exc = await self.expect_error(
                client._request("GET", "/v1/status", token=CAPABILITY),
                BrowserUnavailableError,
            )
            self.assert_redacted(exc)
        finally:
            await client.aclose()

    async def test_transport_error_does_not_leak_the_capability(self) -> None:
        client = self.make_client("transport")
        try:
            exc = await self.expect_error(
                client._request("GET", "/v1/status", token=CAPABILITY),
                BrowserUnavailableError,
            )
            self.assert_redacted(exc)
        finally:
            await client.aclose()

    async def test_server_error_does_not_leak_the_capability(self) -> None:
        client = self.make_client("server")
        try:
            exc = await self.expect_error(
                client._request("GET", "/v1/status", token=CAPABILITY),
                BrowserUnavailableError,
            )
            self.assert_redacted(exc)
        finally:
            await client.aclose()

    async def test_rejected_capability_does_not_leak_the_capability(self) -> None:
        client = self.make_client("capability")
        try:
            exc = await self.expect_error(
                client._request(
                    "POST",
                    "/v1/sessions/brs_1/click",
                    token=CAPABILITY,
                    payload={"action_id": "proof.submit"},
                ),
                BrowserUnavailableError,
            )
            self.assert_redacted(exc)
        finally:
            await client.aclose()

    async def test_policy_error_does_not_leak_the_capability(self) -> None:
        client = self.make_client("policy")
        try:
            exc = await self.expect_error(
                client._request("GET", "/v1/status", token=CAPABILITY),
                BrowserPolicyError,
            )
            self.assertEqual(getattr(exc, "reason", ""), "SUBMIT_TARGET_REQUIRED")
            self.assert_redacted(exc)
        finally:
            await client.aclose()

    async def test_invalid_json_does_not_leak_the_capability(self) -> None:
        client = self.make_client("invalid-json")
        try:
            exc = await self.expect_error(
                client._request("GET", "/v1/status", token=CAPABILITY),
                BrowserUnavailableError,
            )
            self.assert_redacted(exc)
        finally:
            await client.aclose()

    async def test_cancellation_propagates_without_leaking(self) -> None:
        client = self.make_client("timeout")
        try:
            task = asyncio.create_task(
                client._request("GET", "/v1/status", token=CAPABILITY)
            )
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            await client.aclose()


if __name__ == "__main__":
    unittest.main()
