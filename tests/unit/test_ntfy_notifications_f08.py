"""F08.6 no-GMS notification regressions: minimal data and single-attempt delivery."""
from __future__ import annotations

import base64
import json
import os
import unittest
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from personal_assistant.bootstrap import build_container
from personal_assistant.domain import RiskLevel, ToolCall, ToolDescriptor, ToolOutcomeKind
from personal_assistant.infrastructure.memory import InMemorySecretStore
from personal_assistant.settings import ConfigurationError, Settings

TOKEN = "tk_synthetic_f08_ntfy_token"
URL = "http://127.0.0.1:8090/assistant_fixture"
ORIGIN = "https://assistant.example.test"


def defaults() -> Settings:
    with patch.dict(os.environ, {}, clear=True):
        return Settings.from_env()


def receipt(request: httpx.Request) -> dict[str, object]:
    return {
        "id": "fixture12345", "time": 1, "event": "message",
        "topic": request.url.path[1:], "message": request.content.decode(),
        "title": request.headers["Title"], "click": request.headers["Click"],
    }


class NtfySettingsTests(unittest.TestCase):
    def test_disabled_by_default_and_explicit_env_configuration(self) -> None:
        self.assertIsNone(defaults().ntfy_publish_url)
        with patch.dict(os.environ, {
            "PA_NTFY_PUBLISH_URL": URL,
            "PA_NTFY_SECRET_HANDLE": "fixture-handle",
            "PA_PUBLIC_ORIGIN": ORIGIN,
        }, clear=True):
            configured = Settings.from_env()
        self.assertEqual(URL, configured.ntfy_publish_url)
        self.assertEqual("fixture-handle", configured.ntfy_secret_handle)

    def test_partial_configuration_and_unsafe_destinations_are_rejected(self) -> None:
        for changes in (
            {"ntfy_publish_url": URL},
            {"ntfy_secret_handle": "fixture-handle"},
            {"ntfy_publish_url": URL, "ntfy_secret_handle": "fixture-handle"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ConfigurationError):
                replace(defaults(), **changes)
        for url in (
            "https://ntfy.sh/topic", "http://192.168.1.2:8090/topic",
            "http://localhost:8090/topic", "http://127.0.0.1.evil.test:8090/topic",
            "http://user:password@127.0.0.1:8090/topic",
            "http://127.0.0.1:8090/a/b", "http://127.0.0.1:8090/%2e%2e",
            "http://127.0.0.1:8090/topic?secret=x", "http://127.0.0.1:8090/topic#x",
            "http://127.0.0.1:8090/topic\n", "http://127.0.0.1:8090/v1",
        ):
            with self.subTest(url=url), self.assertRaises(ConfigurationError):
                replace(defaults(), ntfy_publish_url=url,
                        ntfy_secret_handle="fixture-handle", public_origin=ORIGIN)


class NtfyNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.secrets = InMemorySecretStore()
        self.handle = await self.secrets.put(
            name="synthetic notification writer", kind="ntfy_publish_token", value=TOKEN,
        )
        self.requests: list[httpx.Request] = []

    def notifier(self, handler: Any) -> Any:
        # Defer import so the initial red run reports the missing implementation per test.
        from personal_assistant.infrastructure.notifications.ntfy import LocalNtfyNotifier
        return LocalNtfyNotifier(
            publish_url=URL, public_origin=ORIGIN,
            secret_handle_id=self.handle.id, secrets=self.secrets,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True),
        )

    async def test_only_minimal_metadata_and_read_only_click_are_sent(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json=receipt(request))
        notifier = self.notifier(handler)
        self.addAsyncCleanup(notifier.aclose)
        report = await notifier.notify_pending(task_id="task_123", risk="R2")
        self.assertEqual((1, 0), (report.accepted, report.unknown))
        self.assertEqual(1, len(self.requests))
        request = self.requests[0]
        self.assertEqual(URL, str(request.url))
        self.assertEqual("POST", request.method)
        self.assertEqual(f"Bearer {TOKEN}", request.headers["Authorization"])
        self.assertEqual(f"{ORIGIN}/ui/?task=task_123", request.headers["Click"])
        self.assertEqual("no", request.headers["Cache"])
        self.assertEqual({"kind": "pending", "risk": "R2", "task_id": "task_123"},
                         json.loads(request.content))
        for forbidden in ("Actions", "Attach", "Email", "Filename", "Delay", "Template"):
            self.assertNotIn(forbidden, request.headers)
        self.assertNotIn(TOKEN, request.content.decode())
        self.assertNotIn(TOKEN, repr(report))

    async def test_bad_receipts_never_confirm_or_trigger_another_post(self) -> None:
        for fault in ("empty", "other_topic", "other_message", "other_click",
                      "actions", "invalid_json", "oversized", "bad_event", "bool_time"):
            requests: list[httpx.Request] = []
            def handler(request: httpx.Request, *, mode: str = fault,
                        captured: list[httpx.Request] = requests) -> httpx.Response:
                captured.append(request)
                data = receipt(request)
                if mode == "empty":
                    data = {}
                elif mode == "other_topic":
                    data["topic"] = "some_other_topic"
                elif mode == "other_message":
                    data["message"] = "different message"
                elif mode == "other_click":
                    data["click"] = "https://other.test/admin"
                elif mode == "actions":
                    data["actions"] = [{"action": "http"}]
                elif mode == "bad_event":
                    data["event"] = "open"
                elif mode == "bool_time":
                    data["time"] = True
                elif mode == "invalid_json":
                    return httpx.Response(200, content=b"not json")
                elif mode == "oversized":
                    return httpx.Response(200, content=b"x" * 20000)
                return httpx.Response(200, json=data)
            with self.subTest(fault=fault):
                notifier = self.notifier(handler)
                try:
                    report = await notifier.notify_pending(task_id="task_123", risk="R2")
                    self.assertEqual((0, 1), (report.accepted, report.unknown))
                    self.assertEqual(1, len(requests))
                finally:
                    await notifier.aclose()

    async def test_timeout_redirect_or_rejection_is_unknown_without_retry(self) -> None:
        for status in (307, 401, 403, 429, 500, None):
            requests: list[httpx.Request] = []
            def handler(request: httpx.Request, *, code: int | None = status,
                        captured: list[httpx.Request] = requests) -> httpx.Response:
                captured.append(request)
                if code is None:
                    raise httpx.ReadTimeout(f"synthetic timeout {TOKEN}", request=request)
                return httpx.Response(code, headers={"Location": "https://other.test/steal"})
            with self.subTest(status=status):
                notifier = self.notifier(handler)
                try:
                    report = await notifier.notify_pending(task_id="task_123", risk="R2")
                    self.assertEqual((0, 1), (report.accepted, report.unknown))
                    self.assertEqual(1, len(requests))
                    self.assertNotIn(TOKEN, repr(report))
                finally:
                    await notifier.aclose()

    async def test_revoked_or_header_injecting_credential_never_sends(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json=receipt(request))
        notifier = self.notifier(handler)
        self.addAsyncCleanup(notifier.aclose)
        await self.secrets.delete(self.handle)
        report = await notifier.notify_pending(task_id="task_123", risk="R2")
        self.assertEqual(0, report.accepted)
        self.assertEqual(1, report.reconfigure_required)
        self.assertEqual([], self.requests)
        injecting = await self.secrets.put(name="synthetic bad token", kind="ntfy_publish_token",
                                          value="value\r\nX-Actions: external")
        notifier._secret_handle_id = injecting.id
        report = await notifier.notify_pending(task_id="task_123", risk="R2")
        self.assertEqual(0, report.accepted)
        self.assertEqual(1, report.reconfigure_required)
        self.assertEqual([], self.requests)

    async def test_metadata_injection_is_rejected_before_reading_secret_or_posting(self) -> None:
        from personal_assistant.domain import ValidationError
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json=receipt(request))
        notifier = self.notifier(handler)
        self.addAsyncCleanup(notifier.aclose)
        for task_id, risk in (("task_1\nActions: http", "R2"), ("../approve", "R2"),
                              ("task_1", "R2\r\n"), ("", "R2")):
            with self.subTest(task_id=task_id, risk=risk), self.assertRaises(ValidationError):
                await notifier.notify_pending(task_id=task_id, risk=risk)
        self.assertEqual([], self.requests)

    async def test_gateway_wires_ntfy_without_web_push_fallback_or_tool_execution(self) -> None:
        configured = replace(defaults(), ntfy_publish_url=URL,
                             ntfy_secret_handle=self.handle.id, public_origin=ORIGIN)
        public = ec.derive_private_key(7, ec.SECP256R1()).public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint,
        )
        configured = replace(configured,
            push_vapid_public_key=base64.urlsafe_b64encode(public).decode().rstrip("="),
            push_vapid_secret_handle="fixture-vapid", push_allowed_origins=("https://push.test",))
        container = build_container(configured, secret_store=self.secrets)
        self.addAsyncCleanup(container.aclose)
        self.assertIsNotNone(container.ntfy)
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            self.assertTrue(container.approvals._repository._records)
            return httpx.Response(200, json=receipt(request))
        await container.ntfy.aclose()
        container.ntfy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        container.tool_registry.publish((ToolDescriptor(
            id="example.send", version="1", extension_id="example.extension",
            extension_version="0.1.0", risk=RiskLevel.EXTERNAL_WRITE,
            input_schema={"type": "object", "additionalProperties": True},
            output_schema={"type": "object", "additionalProperties": True},
        ),))
        with patch.object(container.push, "notify_pending",
                          side_effect=AssertionError("fallback")) as fallback:
            outcome = await container.tool_gateway.invoke(ToolCall(
                tool_id="example.send", tool_version="1", task_id="task_123",
                arguments={"body": "private fixture body"}, target={"to": "private@example.test"},
                workflow_allowed_tools=frozenset({"example.send"}),
            ))
        fallback.assert_not_called()
        self.assertEqual(ToolOutcomeKind.APPROVAL_REQUIRED, outcome.kind)
        self.assertIsNotNone(await container.approvals.get(outcome.approval_id or ""))
        self.assertEqual(1, len(self.requests))
        self.assertNotIn(b"private", self.requests[0].content)

    async def test_ntfy_resource_is_closed_with_container(self) -> None:
        configured = replace(defaults(), ntfy_publish_url=URL,
                             ntfy_secret_handle=self.handle.id, public_origin=ORIGIN)
        container = build_container(configured, secret_store=self.secrets)
        self.assertIsNotNone(container.ntfy)
        client = container.ntfy._client
        await container.aclose()
        self.assertTrue(client.is_closed)

    async def test_duplicate_receipt_fields_cannot_confirm(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            raw = json.dumps(receipt(request))
            return httpx.Response(200, content=raw[:-1] + ', "topic": "assistant_fixture"}')
        notifier = self.notifier(handler)
        self.addAsyncCleanup(notifier.aclose)
        report = await notifier.notify_pending(task_id="task_123", risk="R2")
        self.assertEqual((0, 1), (report.accepted, report.unknown))
        self.assertEqual(1, len(self.requests))

    async def test_postgres_root_wires_ntfy_with_proxy_disabled(self) -> None:
        configured = replace(defaults(), ntfy_publish_url=URL,
                             ntfy_secret_handle=self.handle.id, public_origin=ORIGIN,
                             storage_backend="postgres", environment="production",
                             trust_cloudflare_access=True,
                             cf_access_team_domain="team.cloudflareaccess.com",
                             cf_access_aud="fixture-audience")
        # Building this root does not connect or migrate; real DB gates run separately.
        container = build_container(configured, secret_store=self.secrets)
        self.addAsyncCleanup(container.storage.close)
        self.addAsyncCleanup(container.aclose)
        self.assertIsNotNone(container.ntfy)
        self.assertIs(container.ntfy, container.tool_gateway._pending_notifier)
        self.assertFalse(container.ntfy._client.trust_env)
        self.assertFalse(container.ntfy._client.follow_redirects)
