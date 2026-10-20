"""Failed creates revoke by root authority without a returned session capability."""

from __future__ import annotations

import asyncio
import json
import unittest

import httpx

from personal_assistant.core.browser import BrowserUnavailableError
from personal_assistant.infrastructure.browser.companion_client import LoopbackCompanionClient

ROOT = "synthetic-root-capability"


class FailedCreateClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_creation_receipt_revokes_known_id_with_host_authority(self):
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx.Response(
                200, json={} if request.url.path == "/v1/sessions" else {"revoked": True}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:1"
        ) as http:
            client = LoopbackCompanionClient(
                base_url="http://127.0.0.1:1", root_capability=ROOT, http_client=http
            )
            with self.assertRaises(BrowserUnavailableError):
                await client.create_session(
                    session_id="failed",
                    purpose="fixture",
                    allowed_origins=("https://example.test",),
                    task_id="task",
                    extension_id="fixture",
                )
            self.assertEqual(["/v1/sessions", "/v1/revoke"], [r.url.path for r in requests])
            self.assertEqual({"session_id": "failed"}, json.loads(requests[-1].content))
            self.assertEqual("Bearer " + ROOT, requests[-1].headers["Authorization"])
            self.assertNotIn("failed", client._session_tokens)

    async def test_cancel_without_session_capability_uses_root_revocation(self):
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"revoked": True})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:1"
        ) as http:
            client = LoopbackCompanionClient(
                base_url="http://127.0.0.1:1", root_capability=ROOT, http_client=http
            )
            await client.cancel_session("failed")
            self.assertEqual(1, len(requests))
            self.assertEqual("/v1/revoke", requests[0].url.path)
            self.assertEqual({"session_id": "failed"}, json.loads(requests[0].content))

    async def test_repeated_cancellation_waits_for_failed_create_revocation(self):
        entered, revoking, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        revoked = False

        async def handler(request):
            nonlocal revoked
            if request.url.path == "/v1/sessions":
                entered.set()
                await asyncio.Event().wait()
            revoking.set()
            await release.wait()
            revoked = True
            return httpx.Response(200, json={"revoked": True})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:1"
        ) as http:
            client = LoopbackCompanionClient(
                base_url="http://127.0.0.1:1", root_capability=ROOT, http_client=http
            )
            task = asyncio.create_task(
                client.create_session(
                    session_id="failed",
                    purpose="fixture",
                    allowed_origins=("https://example.test",),
                    task_id="task",
                    extension_id="fixture",
                )
            )
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            try:
                await asyncio.wait_for(revoking.wait(), 1)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(revoked)

    async def test_cleanup_failure_does_not_replace_creation_error_or_block_manual_retry(self):
        revokes = 0

        async def handler(request):
            nonlocal revokes
            if request.url.path == "/v1/sessions":
                return httpx.Response(200, json={})
            revokes += 1
            return httpx.Response(503 if revokes == 1 else 200, json={"revoked": True})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:1"
        ) as http:
            client = LoopbackCompanionClient(
                base_url="http://127.0.0.1:1", root_capability=ROOT, http_client=http
            )
            with self.assertRaisesRegex(BrowserUnavailableError, "did not issue"):
                await client.create_session(
                    session_id="failed",
                    purpose="fixture",
                    allowed_origins=("https://example.test",),
                    task_id="task",
                    extension_id="fixture",
                )
            await client.cancel_session("failed")
            self.assertEqual(2, revokes)


class InvalidCreationIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_id_never_becomes_root_revoke_all(self):
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"revoked": True})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:1"
        ) as http:
            client = LoopbackCompanionClient(
                base_url="http://127.0.0.1:1", root_capability=ROOT, http_client=http
            )
            with self.assertRaises(BrowserUnavailableError):
                await client.create_session(
                    session_id="",
                    purpose="fixture",
                    allowed_origins=("https://example.test",),
                    task_id="task",
                    extension_id="fixture",
                )
            with self.assertRaises(BrowserUnavailableError):
                await client.cancel_session("")
            self.assertEqual([], requests)
