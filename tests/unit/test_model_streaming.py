"""Real incremental provider protocol with synthetic HTTP transport; no vendor calls."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace

import httpx

from personal_assistant.core.models import (
    ContextField,
    DataClassification,
    DisclosureDenied,
    ModelRequest,
    ModelRouter,
)
from personal_assistant.core.models.errors import (
    ModelProviderProtocolError,
    ModelProviderRejectedError,
    ModelProviderResponseTooLargeError,
    ModelProviderTimeoutError,
    ModelProviderUnavailableError,
)
from personal_assistant.infrastructure.memory import InMemoryAuditWriter
from personal_assistant.infrastructure.memory.secrets import InMemorySecretStore
from personal_assistant.infrastructure.models.openai_compatible import (
    OpenAICompatibleChatProvider,
    OpenAICompatibleConfig,
)


def chunk(text=None, finish=None, usage=None):
    delta = {} if text is None else {"content": text}
    value = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        value["usage"] = usage
    return b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\r\n\r\n"


class Stream(httpx.AsyncByteStream):
    def __init__(self, parts, *, gate=None):
        self.parts, self.gate = parts, gate
        self.closed = False
        self.read = 0

    async def __aiter__(self):
        for index, part in enumerate(self.parts):
            if index == 1 and self.gate is not None:
                await self.gate.wait()
            self.read += 1
            yield part

    async def aclose(self):
        self.closed = True


class ModelStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def provider(self, stream, *, status=200, **options):
        self.requests = []

        async def handle(request):
            self.requests.append(request)
            return httpx.Response(
                status, stream=stream, headers={"Content-Type": "text/event-stream"}
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True)
        self.addAsyncCleanup(client.aclose)
        secrets = InMemorySecretStore()
        secret = await secrets.put(name="synthetic", kind="model_api_key", value="test-only-key")
        return OpenAICompatibleChatProvider(
            OpenAICompatibleConfig(
                provider_id="remote",
                model_id="synthetic",
                base_url="https://model.example.test",
                secret_handle=secret,
                json_mode=True,
                **options,
            ),
            secret_store=secrets,
            http_client=client,
        )

    async def test_first_delta_arrives_before_provider_finishes_and_audit_waits(self):
        gate = asyncio.Event()
        wire = Stream(
            [
                chunk("你好"),
                chunk("，世界"),
                chunk(finish="stop", usage={"total_tokens": 7, "private": 99}),
                b"data: [DONE]\n\n",
            ],
            gate=gate,
        )
        provider = await self.provider(wire)
        audit = InMemoryAuditWriter()
        router = ModelRouter((provider,), audit=audit)
        request = ModelRequest("workbench.chat", "answer", metadata={"response_format": "text"})
        events = router.stream(request, provider_id="remote")
        first = await anext(events)
        self.assertEqual(first.delta, "你好")
        self.assertEqual(wire.read, 1)
        self.assertFalse(wire.closed)
        self.assertEqual(await audit.list_events(), [])
        gate.set()
        rest = [event async for event in events]
        self.assertEqual(rest[-1].output.text, "你好，世界")
        self.assertEqual(rest[-1].output.usage, {"total_tokens": 7})
        self.assertTrue(wire.closed)
        payload = json.loads(self.requests[0].content)
        self.assertTrue(payload["stream"])
        self.assertTrue(payload["stream_options"]["include_usage"])
        self.assertNotIn("response_format", payload)
        self.assertEqual(len(await audit.list_events()), 1)

    async def test_eof_without_done_never_returns_success(self):
        wire = Stream([chunk("partial"), chunk(finish="stop")])
        provider = await self.provider(wire)
        events = []
        with self.assertRaises(ModelProviderProtocolError):
            async for event in provider.stream(ModelRequest("chat", "answer")):
                events.append(event)
        self.assertEqual([e.delta for e in events], ["partial"])
        self.assertTrue(wire.closed)

    async def test_truncated_completion_is_not_success(self):
        wire = Stream([chunk("partial"), chunk(finish="length"), b"data: [DONE]\n\n"])
        provider = await self.provider(wire)
        with self.assertRaises(ModelProviderProtocolError):
            _ = [event async for event in provider.stream(ModelRequest("chat", "answer"))]
        self.assertTrue(wire.closed)

    async def test_utf8_across_chunk_boundaries(self):
        data = chunk("你好") + chunk(finish="stop") + b"data: [DONE]\n\n"
        wire = Stream([bytes([byte]) for byte in data])
        provider = await self.provider(wire)
        events = [event async for event in provider.stream(ModelRequest("chat", "answer"))]
        self.assertEqual(events[-1].output.text, "你好")

    async def test_caller_closes_stream_closes_transport(self):
        wire = Stream([chunk("first"), chunk("second")])
        provider = await self.provider(wire)
        events = ModelRouter((provider,)).stream(
            ModelRequest("chat", "answer"), provider_id="remote"
        )
        await anext(events)
        await events.aclose()
        self.assertTrue(wire.closed)

    async def test_reasoning_wire_over_old_limit_preserves_complete_answer(self):
        # Wire size includes reasoning and repeated JSON envelopes, not just answer text.
        reasoning = b'data: {"choices":[{"delta":{"reasoning_content":"' + b'x' * 65536
        reasoning += b'"},"finish_reason":null}]}\n\n'
        wire = Stream([reasoning] * 18 + [chunk("complete"), chunk(finish="stop"),
                                         b"data: [DONE]\n\n"])
        provider = await self.provider(wire)
        events = [event async for event in provider.stream(ModelRequest("chat", "answer"))]
        self.assertEqual(events[-1].output.text, "complete")
        self.assertEqual([event.delta for event in events if event.delta], ["complete"])
        self.assertTrue(wire.closed)
        self.assertEqual(wire.read, 21)
        self.assertNotIn("max_tokens", json.loads(self.requests[0].content))

    async def test_total_deadline_cancels_pending_read(self):
        wire = Stream([chunk("first"), chunk("second")], gate=asyncio.Event())
        provider = await self.provider(wire, timeout_seconds=0.05)
        with self.assertRaises(ModelProviderTimeoutError):
            _ = [event async for event in provider.stream(ModelRequest("chat", "answer"))]
        self.assertTrue(wire.closed)

    async def test_size_bound_counts_all_chunks(self):
        wire = Stream([b":" + b"x" * 600 + b"\n\n", b":" + b"x" * 600 + b"\n\n"])
        provider = await self.provider(wire, max_response_bytes=1024)
        with self.assertRaises(ModelProviderResponseTooLargeError):
            _ = [event async for event in provider.stream(ModelRequest("chat", "answer"))]
        self.assertTrue(wire.closed)

    async def test_redirect_does_not_dispatch_again(self):
        wire = Stream([b"private vendor error"])
        provider = await self.provider(wire, status=302)
        with self.assertRaises(ModelProviderRejectedError) as caught:
            _ = [event async for event in provider.stream(ModelRequest("chat", "answer"))]
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(wire.closed)

    async def test_secret_denied_before_adapter_auth_or_network(self):
        provider = await self.provider(Stream([]))
        request = ModelRequest(
            "chat", "answer", (ContextField("s", "secret", DataClassification.SECRET, "test"),)
        )
        for source in (
            provider.stream(request),
            ModelRouter((provider,)).stream(request, provider_id="remote"),
        ):
            with self.assertRaises(DisclosureDenied):
                _ = [event async for event in source]
        self.assertEqual(self.requests, [])

    async def test_protected_request_and_recipient_drift_denied(self):
        provider = await self.provider(Stream([]))
        router = ModelRouter((provider,))
        request = ModelRequest(
            "chat", "answer", (ContextField("p", "private", DataClassification.PERSONAL, "test"),)
        )
        with self.assertRaises(DisclosureDenied):
            _ = [e async for e in router.stream(request, provider_id="remote")]
        provider._recipient = replace(provider.recipient, endpoint="https://elsewhere.example.test")
        with self.assertRaises(DisclosureDenied):
            _ = [
                e async for e in router.stream(ModelRequest("chat", "answer"), provider_id="remote")
            ]
        self.assertEqual(self.requests, [])

    async def test_cancel_pending_response_closes_and_does_not_audit(self):
        wire = Stream([chunk("first"), chunk("second")], gate=asyncio.Event())
        provider = await self.provider(wire)
        audit = InMemoryAuditWriter()
        events = ModelRouter((provider,), audit=audit).stream(
            ModelRequest("chat", "answer"), provider_id="remote"
        )
        await anext(events)
        pending = asyncio.create_task(anext(events))
        await asyncio.sleep(0)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(wire.closed)
        self.assertEqual(await audit.list_events(), [])

    async def test_close_failure_after_done_never_reports_success(self):
        class BadClose(Stream):
            async def aclose(self):
                self.closed = True
                raise OSError("private transport detail")

        wire = BadClose([chunk("first"), chunk(finish="stop"), b"data: [DONE]\n\n"])
        provider = await self.provider(wire)
        with self.assertRaises(ModelProviderUnavailableError) as caught:
            _ = [e async for e in provider.stream(ModelRequest("chat", "answer"))]
        self.assertNotIn("private", str(caught.exception))
        self.assertIsNone(caught.exception.__context__)

    async def test_malformed_json_does_not_expose_vendor_text(self):
        wire = Stream([b"data: PRIVATE_INVALID_JSON\n\n"])
        provider = await self.provider(wire)
        with self.assertRaises(ModelProviderProtocolError) as caught:
            _ = [e async for e in provider.stream(ModelRequest("chat", "answer"))]
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertIsNone(caught.exception.__context__)
        self.assertTrue(wire.closed)

    async def test_persisted_disclosure_and_final_usage_are_audited_once(self):
        from personal_assistant.core.models.disclosure import DisclosureConsentService
        from personal_assistant.infrastructure.memory import InMemoryDisclosureConsentStore

        wire = Stream(
            [
                chunk("ok"),
                chunk(finish="stop"),
                b'data: {"choices":[],"usage":{"total_tokens":5}}\n\n',
                b"data: [DONE]\n\n",
            ]
        )
        provider = await self.provider(wire)
        service = DisclosureConsentService(
            InMemoryDisclosureConsentStore(), recipients={"remote": provider.recipient}
        )
        request = ModelRequest(
            "chat", "answer", (ContextField("p", "private", DataClassification.SENSITIVE, "test"),)
        )
        preview = service.preview(request, provider_id="remote")
        consent = await service.confirm(
            request,
            provider_id="remote",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="one",
        )
        audit = InMemoryAuditWriter()
        router = ModelRouter((provider,), disclosure=service, audit=audit)
        events = [
            e
            async for e in router.stream(
                request, provider_id="remote", consent_id=consent.id, user_id="owner"
            )
        ]
        self.assertEqual(events[-1].output.usage, {"total_tokens": 5})
        records = await audit.list_events()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].data["consent_id"], consent.id)
        self.assertNotIn("private", json.dumps(records[0].data))
