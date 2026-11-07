from __future__ import annotations

import unittest

import httpx

from personal_assistant.cli.tui.service import WorkbenchError, WorkbenchService


class WorkbenchServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_configuration_binds_digest_and_version(self):
        calls = []

        async def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"application": {"state": "APPLYING"}})

        service = WorkbenchService(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(service.aclose)
        result = await service.apply_extension_config("fixture.ext", "1.0.0", "a" * 64, "apply-key")
        self.assertEqual("APPLYING", result["application"]["state"])
        self.assertEqual("/admin/v1/extensions/fixture.ext/config/apply", calls[0].url.path)
        self.assertEqual("apply-key", calls[0].headers["Idempotency-Key"])
        self.assertIn(b'"expected_digest":"' + b"a" * 64 + b'"', calls[0].content)
        self.assertIn(b'"extension_version":"1.0.0"', calls[0].content)

    async def test_reject_non_loopback_credentials_query_path(self):
        for url in (
            "https://remote.test",
            "http://127.0.0.1.evil",
            "http://user:pass@localhost",
            "http://localhost/path",
            "http://localhost?x=1",
            "file:///tmp/x",
        ):
            with self.subTest(url=url), self.assertRaises(WorkbenchError):
                WorkbenchService(url)

    async def test_uncertain_write_retries_exact_body_and_key_blocks_other_mutation(self):
        calls = []

        async def respond(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ReadTimeout("not received", request=request)
            return httpx.Response(202, json={"id": "task_fixture"})

        service = WorkbenchService(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(service.aclose)
        with self.assertRaises(WorkbenchError) as failure:
            await service.create_task("synthetic", "first-key")
        self.assertTrue(failure.exception.uncertain)
        self.assertIsNotNone(service.pending)
        with self.assertRaises(WorkbenchError):
            await service.create_task("another", "new-key")
        self.assertEqual(1, len(calls))
        result = await service.retry_pending()
        self.assertEqual("task_fixture", result["id"])
        self.assertEqual(calls[0].content, calls[1].content)
        self.assertEqual("first-key", calls[1].headers["Idempotency-Key"])
        self.assertIsNone(service.pending)

    async def test_no_redirect_following_and_known_conflict_can_refresh(self):
        calls = []

        async def respond(request):
            calls.append(request)
            if request.url.path.endswith("/tasks"):
                return httpx.Response(409, json={"error": {"code": "conflict", "message": "stale"}})
            return httpx.Response(302, headers={"Location": "https://evil.example"})

        service = WorkbenchService(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(service.aclose)
        with self.assertRaises(WorkbenchError) as failure:
            await service.create_task("synthetic", "key")
        self.assertFalse(failure.exception.uncertain)
        self.assertIsNone(service.pending)
        with self.assertRaises(WorkbenchError):
            await service.status()
        self.assertEqual(2, len(calls))

    async def test_exact_install_and_task_route_reuse(self):
        calls = []

        async def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"id": "op"})

        service = WorkbenchService(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(service.aclose)
        preview = {
            "plan_id": "plan",
            "confirmation_nonce": "synthetic",
            "preview_hash": "hash",
            "mode": "install",
            "extension_id": "fixture.ext",
        }
        await service.install_extension(preview, "install-key")
        self.assertEqual("/admin/v1/extensions/install", calls[-1].url.path)
        self.assertIn(b'"accepted_warning":true', calls[-1].content)
        await service.resume_task("task", 3, "resume-key", consent_id="consent")
        self.assertEqual("/admin/v1/workbench/tasks/task/execution", calls[-1].url.path)
        self.assertIn(b'"consent_id":"consent"', calls[-1].content)
        self.assertEqual("1", calls[-1].headers["X-Assistant-Workbench"])

    async def test_server_failure_does_not_allow_new_write(self):
        async def respond(request):
            return httpx.Response(503, json={"error": {"code": "unavailable"}})

        service = WorkbenchService(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(service.aclose)
        with self.assertRaises(WorkbenchError) as failure:
            await service.create_task("fixture", "key")
        self.assertTrue(failure.exception.uncertain)
        self.assertIsNotNone(service.pending)

    async def test_config_unknown_write_does_not_repeat_without_server_receipt(self):
        calls = []

        async def respond(request):
            calls.append(request)
            raise httpx.ReadTimeout("unknown", request=request)

        service = WorkbenchService(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(service.aclose)
        with self.assertRaises(WorkbenchError):
            await service.save_extension_config("fixture", {"prefix": "x"}, "config")
        self.assertFalse(service.pending["retryable"])
        with self.assertRaises(WorkbenchError) as error:
            await service.retry_pending()
        self.assertEqual("NO_SAFE_RETRY", error.exception.code)
        self.assertEqual(1, len(calls))

    async def test_chat_terminal_failure_resolves_pending_unknown_keeps_original_key(self):
        replies = [
            httpx.Response(502, json={"error": {"code": "CHAT_RESPONSE_INVALID"}}),
            httpx.Response(409, json={"error": {"code": "CHAT_RESULT_UNKNOWN"}}),
        ]

        async def respond(request):
            return replies.pop(0)

        service = WorkbenchService(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(service.aclose)
        with self.assertRaises(WorkbenchError) as known:
            await service.chat_confirm("fixture", [], "a" * 64, "known")
        self.assertFalse(known.exception.uncertain)
        self.assertIsNone(service.pending)
        with self.assertRaises(WorkbenchError) as unknown:
            await service.chat_confirm("fixture", [], "b" * 64, "unknown")
        self.assertTrue(unknown.exception.uncertain)
        self.assertIsNotNone(service.pending)
