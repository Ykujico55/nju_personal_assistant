"""F04 local/remote HTTP model adapters: typed failures and resource safety.

All HTTP is served by an in-process ``httpx.MockTransport``.  These tests prove
protocol handling, fail-closed credential resolution, bounded responses and
that credentials never appear in requests, errors or fixtures.  They do not
claim a vendor end-to-end validation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import traceback
import unittest
from enum import StrEnum
from typing import Any
from unittest.mock import patch

import httpx

from personal_assistant.core.models import (
    ContextField,
    DataClassification,
    DisclosureDenied,
    ModelRequest,
    RecipientIdentity,
)
from personal_assistant.core.models.errors import (
    ModelCredentialUnavailableError,
    ModelProviderProtocolError,
    ModelProviderRejectedError,
    ModelProviderResponseTooLargeError,
    ModelProviderTimeoutError,
    ModelProviderUnavailableError,
)
from personal_assistant.core.secrets import SecretHandle
from personal_assistant.domain import ValidationError
from personal_assistant.infrastructure.memory import InMemorySecretStore
from personal_assistant.infrastructure.models import (
    OllamaChatProvider,
    OllamaConfig,
    OpenAICompatibleChatProvider,
    OpenAICompatibleConfig,
)
from personal_assistant.settings import ConfigurationError

FAKE_KEY = "sk-test-not-a-real-key-0123456789"
FAKE_KEY_2 = "sk-test-other-key-abcdef"
SENSITIVE_MARKER = "hello from fixture"


class ForeignClassification(StrEnum):
    """A SECRET-valued StrEnum that is not the core ``DataClassification``."""

    SECRET = "SECRET"


def sample_request() -> ModelRequest:
    return ModelRequest(
        purpose="draft reply",
        instruction="draft a reply",
        fields=(
            ContextField("mail_body", "hello from fixture", DataClassification.SENSITIVE, "mail:1"),
        ),
    )


class LoopbackProxyProbe:
    """A loopback stand-in for an environment HTTP proxy that records traffic.

    A correctly configured adapter must never hand loopback or credential-
    bearing requests to ``HTTP_PROXY``/``HTTPS_PROXY``; this probe fails the
    test when anything arrives.
    """

    def __init__(self) -> None:
        self.received = bytearray()
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=1.5)
            self.received.extend(chunk)
        except (TimeoutError, ConnectionError, OSError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


def proxy_environment(port: int) -> dict[str, str]:
    url = f"http://127.0.0.1:{port}"
    return {
        "HTTP_PROXY": url,
        "http_proxy": url,
        "HTTPS_PROXY": url,
        "https_proxy": url,
        "ALL_PROXY": url,
        "all_proxy": url,
        "NO_PROXY": "",
        "no_proxy": "",
    }


class SlowCloseStream(httpx.AsyncByteStream):
    """Stream whose ``aclose`` is slow enough to be interrupted by a cancel."""

    def __init__(self, tracker: dict[str, int]) -> None:
        self._tracker = tracker

    async def __aiter__(self) -> Any:
        yield b"partial"
        await asyncio.sleep(5.0)

    async def aclose(self) -> None:
        self._tracker["closed"] = self._tracker.get("closed", 0) + 1
        await asyncio.sleep(0.15)
        self._tracker["closed_ok"] = self._tracker.get("closed_ok", 0) + 1


class FailingCloseStream(httpx.AsyncByteStream):
    """Stream whose ``aclose`` raises, to prove cleanup cannot mask errors."""

    def __init__(
        self,
        chunks: list[bytes],
        tracker: dict[str, int],
        *,
        delay: float = 0.0,
        marker: str = "raw-close-marker",
    ) -> None:
        self._chunks = chunks
        self._tracker = tracker
        self._delay = delay
        self._marker = marker

    async def __aiter__(self) -> Any:
        for chunk in self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield chunk

    async def aclose(self) -> None:
        self._tracker["closed"] = self._tracker.get("closed", 0) + 1
        raise RuntimeError(self._marker)


def frame_locals(error: BaseException) -> str:
    """Render every traceback frame's locals, as an error reporter would."""

    rendered: list[str] = []
    tb = error.__traceback__
    while tb is not None:
        for key, value in tb.tb_frame.f_locals.items():
            rendered.append(f"{key}={value!r}")
        tb = tb.tb_next
    return "\n".join(rendered)


class TrackingStream(httpx.AsyncByteStream):
    def __init__(
        self, chunks: list[bytes], tracker: dict[str, int], *, delay: float = 0.0
    ) -> None:
        self._chunks = chunks
        self._tracker = tracker
        self._delay = delay

    async def __aiter__(self) -> Any:
        for chunk in self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield chunk

    async def aclose(self) -> None:
        self._tracker["closed"] = self._tracker.get("closed", 0) + 1


def openai_body(text: str = "draft output") -> bytes:
    return json.dumps(
        {
            "model": "gpt-test-1",
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }
    ).encode("utf-8")


class RemoteAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.secret_store = InMemorySecretStore()
        self.handle = await self.secret_store.put(
            name="remote-model", kind="model_api_key", value=FAKE_KEY
        )
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []

    def build(
        self,
        handler: Any,
        *,
        timeout: float = 2.0,
        max_bytes: int = 64 * 1024,
        client: httpx.AsyncClient | None = None,
    ) -> OpenAICompatibleChatProvider:
        def wrapped(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.content:
                self.bodies.append(json.loads(request.content))
            return handler(request)

        transport = httpx.MockTransport(wrapped)
        return OpenAICompatibleChatProvider(
            OpenAICompatibleConfig(
                provider_id="remote.openai",
                model_id="gpt-test-1",
                base_url="https://api.example.test/v1",
                timeout_seconds=timeout,
                max_response_bytes=max_bytes,
                secret_handle=self.handle,
            ),
            secret_store=self.secret_store,
            http_client=client or httpx.AsyncClient(transport=transport),
        )

    async def test_successful_call_uses_credential_and_parses_output(self) -> None:
        provider = self.build(lambda request: httpx.Response(200, content=openai_body()))
        output = await provider.complete(sample_request())
        self.assertEqual("draft output", output.text)
        self.assertEqual("remote.openai", output.provider_id)
        self.assertEqual("gpt-test-1", output.model_id)
        self.assertEqual(10, output.usage["total_tokens"])

        self.assertEqual(1, len(self.requests))
        request = self.requests[0]
        self.assertEqual("https://api.example.test/v1/chat/completions", str(request.url))
        self.assertEqual(f"Bearer {FAKE_KEY}", request.headers["authorization"])
        body = self.bodies[0]
        self.assertEqual("gpt-test-1", body["model"])
        self.assertFalse(body["stream"])
        serialized = json.dumps(body)
        self.assertIn("hello from fixture", serialized)
        self.assertNotIn(self.handle.id, serialized)
        self.assertNotIn("SecretHandle", serialized)

    async def test_credential_resolution_failure_sends_no_request(self) -> None:
        empty_store = InMemorySecretStore()
        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(200, content=openai_body())

        provider = OpenAICompatibleChatProvider(
            OpenAICompatibleConfig(
                provider_id="remote.openai",
                model_id="gpt-test-1",
                base_url="https://api.example.test/v1",
                secret_handle=SecretHandle(id="missing-handle", kind="model_api_key"),
            ),
            secret_store=empty_store,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with self.assertRaises(ModelCredentialUnavailableError) as caught:
            await provider.complete(sample_request())
        self.assertEqual(0, len(sent))
        self.assertNotIn(FAKE_KEY, str(caught.exception))

    async def test_non_success_status_is_typed_and_never_leaks_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                429,
                json={
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": f"key {FAKE_KEY_2} is over quota",
                    }
                },
            )

        provider = self.build(handler)
        with self.assertRaises(ModelProviderRejectedError) as caught:
            await provider.complete(sample_request())
        error = caught.exception
        self.assertEqual(429, error.status_code)
        self.assertEqual("rate_limited", error.rejection_code)
        self.assertNotIn(FAKE_KEY_2, str(error))
        self.assertNotIn("rate_limit_exceeded", repr(vars(error)))
        self.assertEqual(1, len(self.requests))

    async def test_malformed_and_incomplete_responses_are_protocol_errors(self) -> None:
        for label, payload in (
            ("not-json", b"<html>gateway error</html>"),
            ("missing-content", json.dumps({"choices": [{"message": {}}]}).encode()),
            ("empty-content", json.dumps({"choices": [{"message": {"content": ""}}]}).encode()),
            ("wrong-shape", json.dumps({"choices": "nope"}).encode()),
        ):
            with self.subTest(label=label):
                provider = self.build(lambda request, p=payload: httpx.Response(200, content=p))
                with self.assertRaises(ModelProviderProtocolError):
                    await provider.complete(sample_request())

    async def test_oversized_response_is_rejected_and_stream_closed(self) -> None:
        tracker: dict[str, int] = {}
        provider = self.build(
            lambda request: httpx.Response(
                200, stream=TrackingStream([b"x" * 2048] * 8, tracker)
            ),
            max_bytes=4096,
        )
        with self.assertRaises(ModelProviderResponseTooLargeError):
            await provider.complete(sample_request())
        self.assertGreaterEqual(tracker.get("closed", 0), 1)

    async def test_slow_response_times_out_and_closes_stream(self) -> None:
        tracker: dict[str, int] = {}
        provider = self.build(
            lambda request: httpx.Response(
                200, stream=TrackingStream([b"a", b"b"], tracker, delay=1.0)
            ),
            timeout=0.15,
        )
        with self.assertRaises(ModelProviderTimeoutError):
            await provider.complete(sample_request())
        self.assertGreaterEqual(tracker.get("closed", 0), 1)
        self.assertEqual(1, len(self.requests))

    async def test_cancellation_propagates_and_closes_stream(self) -> None:
        tracker: dict[str, int] = {}
        started = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            del request
            started.set()
            return httpx.Response(200, stream=TrackingStream([b"a"], tracker, delay=5.0))

        provider = self.build(handler)
        task = asyncio.create_task(provider.complete(sample_request()))
        await started.wait()
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertGreaterEqual(tracker.get("closed", 0), 1)

    async def test_redirect_is_not_followed_even_with_redirecting_client(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                302, headers={"Location": "https://evil.example.test/steal"}
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        )
        provider = self.build(lambda request: handler(request), client=client)
        with self.assertRaises(ModelProviderRejectedError):
            await provider.complete(sample_request())
        self.assertEqual(1, calls)

    async def test_provider_is_never_retried_silently(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503, json={"error": {"code": "overloaded"}})

        provider = self.build(handler)
        with self.assertRaises(ModelProviderRejectedError):
            await provider.complete(sample_request())
        self.assertEqual(1, calls)

    async def test_negative_usage_counters_are_dropped(self) -> None:
        payload = {
            "model": "gpt-test-1",
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": -7, "completion_tokens": 3},
        }
        provider = self.build(lambda request: httpx.Response(200, json=payload))
        output = await provider.complete(sample_request())
        self.assertNotIn("prompt_tokens", output.usage)
        self.assertEqual(3, output.usage["completion_tokens"])

    async def test_credential_broker_failure_keeps_no_exception_chain(self) -> None:
        class ExplodingStore:
            async def put(self, *, name: str, kind: str, value: str) -> SecretHandle:
                raise AssertionError("not used")

            async def resolve_for_broker(
                self, handle: SecretHandle, *, purpose: str
            ) -> str:
                raise ValueError(f"broker leaked {FAKE_KEY} for {purpose}")

            async def delete(self, handle: SecretHandle) -> None:
                raise AssertionError("not used")

        provider = OpenAICompatibleChatProvider(
            OpenAICompatibleConfig(
                provider_id="remote.openai",
                model_id="gpt-test-1",
                base_url="https://api.example.test/v1",
                secret_handle=self.handle,
            ),
            secret_store=ExplodingStore(),
        )
        with self.assertRaises(ModelCredentialUnavailableError) as caught:
            await provider.complete(sample_request())
        error = caught.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        self.assertNotIn(FAKE_KEY, rendered)

    async def test_transport_failure_keeps_no_request_in_exception_chain(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        provider = self.build(handler)
        with self.assertRaises(ModelProviderUnavailableError) as caught:
            await provider.complete(sample_request())
        error = caught.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertFalse(
            any(isinstance(value, httpx.Request) for value in vars(error).values())
        )
        rendered = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        self.assertNotIn(FAKE_KEY, rendered)
        self.assertNotIn("hello from fixture", rendered)
        self.assertNotIn("authorization", rendered.lower())

    async def test_timeout_failure_keeps_no_exception_chain(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        provider = self.build(handler)
        with self.assertRaises(ModelProviderTimeoutError) as caught:
            await provider.complete(sample_request())
        error = caught.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        self.assertNotIn(FAKE_KEY, rendered)
        self.assertNotIn("hello from fixture", rendered)

    async def test_malformed_json_keeps_no_response_body_in_exception_chain(self) -> None:
        body = f"not-json {FAKE_KEY} hello from fixture".encode()
        provider = self.build(lambda request: httpx.Response(200, content=body))
        with self.assertRaises(ModelProviderProtocolError) as caught:
            await provider.complete(sample_request())
        error = caught.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        self.assertNotIn(FAKE_KEY, rendered)
        self.assertNotIn("hello from fixture", rendered)

    async def test_repeated_cancellation_still_closes_the_response_stream(self) -> None:
        tracker: dict[str, int] = {}
        started = asyncio.Event()

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            started.set()
            return httpx.Response(200, stream=SlowCloseStream(tracker))

        provider = self.build(handler)
        task = asyncio.create_task(provider.complete(sample_request()))
        await started.wait()
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(1, tracker.get("closed", 0))
        self.assertEqual(1, tracker.get("closed_ok", 0))

    async def test_http_level_timeout_is_typed_as_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        provider = self.build(handler)
        with self.assertRaises(ModelProviderTimeoutError):
            await provider.complete(sample_request())
        self.assertEqual(1, len(self.requests))

    async def test_connection_failure_is_typed_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        provider = self.build(handler)
        with self.assertRaises(ModelProviderUnavailableError) as caught:
            await provider.complete(sample_request())
        self.assertNotIn(FAKE_KEY, str(caught.exception))
        self.assertNotIn("hello from fixture", str(caught.exception))

    async def test_remote_adapter_ignores_environment_proxies(self) -> None:
        probe = LoopbackProxyProbe()
        await probe.start()
        try:
            with patch.dict(os.environ, proxy_environment(probe.port), clear=False):
                provider = OpenAICompatibleChatProvider(
                    OpenAICompatibleConfig(
                        provider_id="remote.openai",
                        model_id="gpt-test-1",
                        base_url="https://127.0.0.1:1/v1",
                        timeout_seconds=1.0,
                        secret_handle=self.handle,
                    ),
                    secret_store=self.secret_store,
                )
                try:
                    with self.assertRaises(ModelProviderUnavailableError):
                        await provider.complete(sample_request())
                finally:
                    await provider.aclose()
        finally:
            await probe.stop()
        self.assertEqual(
            b"",
            bytes(probe.received),
            "remote adapter sent traffic through HTTP_PROXY/HTTPS_PROXY",
        )

    async def test_invalid_configuration_is_rejected_at_construction(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=openai_body())

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        bad_configs = (
            {"base_url": "http://api.example.test/v1"},
            {"base_url": "https://user:pass@api.example.test/v1"},
            {"base_url": "https://api.example.test/v1?key=1"},
            {"model_id": "  "},
            {"timeout_seconds": 0.0},
            {"secret_handle": None},
        )
        base: dict[str, Any] = {
            "provider_id": "remote.openai",
            "model_id": "gpt-test-1",
            "base_url": "https://api.example.test/v1",
            "secret_handle": self.handle,
        }
        for override in bad_configs:
            with self.subTest(override=override), self.assertRaises(ConfigurationError):
                OpenAICompatibleChatProvider(
                    OpenAICompatibleConfig(**{**base, **override}),
                    secret_store=self.secret_store,
                    http_client=client,
                )
        with self.assertRaises(ConfigurationError):
            OpenAICompatibleChatProvider(
                OpenAICompatibleConfig(**base), secret_store=None, http_client=client
            )

    async def test_adapter_refuses_secret_fields_before_sending(self) -> None:
        provider = self.build(lambda request: httpx.Response(200, content=openai_body()))
        secret_request = ModelRequest(
            purpose="login",
            instruction="use the credential",
            fields=(ContextField("password", "hunter2", DataClassification.SECRET, "vault"),),
        )

        with self.assertRaises(DisclosureDenied):
            await provider.complete(secret_request)
        self.assertEqual(0, len(self.requests))

    async def test_response_metadata_cannot_inject_audit_identity(self) -> None:
        payload = {
            "model": "hello from fixture",
            "choices": [{"message": {"content": "answer"}}],
            "usage": {
                "prompt_tokens": 1,
                "hello from fixture": 2,
                "evil.persistent.key": 3,
            },
        }
        provider = self.build(lambda request: httpx.Response(200, json=payload))
        output = await provider.complete(sample_request())
        self.assertEqual("gpt-test-1", output.model_id)
        self.assertEqual({"prompt_tokens": 1}, dict(output.usage))

    async def test_rejection_code_is_a_fixed_enum_not_echoed_vendor_text(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "hello from fixture",
                        "type": "hello from fixture",
                    }
                },
            )

        provider = self.build(handler)
        with self.assertRaises(ModelProviderRejectedError) as caught:
            await provider.complete(sample_request())
        error = caught.exception
        self.assertEqual("bad_request", error.rejection_code)
        self.assertNotIn("hello from fixture", str(error))
        self.assertNotIn("hello from fixture", repr(vars(error)))

    async def test_transport_failure_leaves_no_secret_in_traceback_locals(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        provider = self.build(handler)
        with self.assertRaises(ModelProviderUnavailableError) as caught:
            await provider.complete(sample_request())
        probe = frame_locals(caught.exception)
        self.assertNotIn(FAKE_KEY, probe)
        self.assertNotIn("hello from fixture", probe)
        self.assertNotIn("authorization", probe.lower())

    async def test_non_ascii_credential_is_rejected_before_http_encoding(self) -> None:
        leaked = "\u043a\u043b\u044e\u0447-\u4e2d\u6587-key"

        class UnicodeStore:
            async def put(self, *, name: str, kind: str, value: str) -> SecretHandle:
                raise AssertionError("not used")

            async def resolve_for_broker(
                self, handle: SecretHandle, *, purpose: str
            ) -> str:
                del handle, purpose
                return leaked

            async def delete(self, handle: SecretHandle) -> None:
                raise AssertionError("not used")

        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(200, content=openai_body())

        provider = OpenAICompatibleChatProvider(
            OpenAICompatibleConfig(
                provider_id="remote.openai",
                model_id="gpt-test-1",
                base_url="https://api.example.test/v1",
                secret_handle=self.handle,
            ),
            secret_store=UnicodeStore(),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with self.assertRaises(ModelCredentialUnavailableError) as caught:
            await provider.complete(sample_request())
        error = caught.exception
        self.assertEqual([], sent)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        self.assertNotIn(leaked, rendered)
        self.assertNotIn(leaked, frame_locals(error))

    async def test_close_failure_does_not_override_cancellation(self) -> None:
        tracker: dict[str, int] = {}
        started = asyncio.Event()

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            started.set()
            return httpx.Response(
                200, stream=FailingCloseStream([b"a"], tracker, delay=5.0)
            )

        provider = self.build(handler)
        task = asyncio.create_task(provider.complete(sample_request()))
        await started.wait()
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertGreaterEqual(tracker.get("closed", 0), 1)

    async def test_close_failure_does_not_override_original_typed_error(self) -> None:
        tracker: dict[str, int] = {}
        provider = self.build(
            lambda request: httpx.Response(
                200, stream=FailingCloseStream([b"x" * 2048] * 8, tracker)
            ),
            max_bytes=4096,
        )
        with self.assertRaises(ModelProviderResponseTooLargeError) as caught:
            await provider.complete(sample_request())
        self.assertNotIn("raw-close-marker", frame_locals(caught.exception))
        self.assertGreaterEqual(tracker.get("closed", 0), 1)

    async def test_close_failure_without_other_error_is_sanitized_typed(self) -> None:
        tracker: dict[str, int] = {}
        provider = self.build(
            lambda request: httpx.Response(
                200, stream=FailingCloseStream([openai_body()], tracker)
            )
        )
        with self.assertRaises(ModelProviderUnavailableError) as caught:
            await provider.complete(sample_request())
        error = caught.exception
        rendered = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        self.assertNotIn("raw-close-marker", rendered)
        self.assertNotIn("raw-close-marker", frame_locals(error))
        self.assertEqual(1, tracker.get("closed", 0))

    async def test_foreign_and_string_secret_classifications_are_rejected(self) -> None:
        for label, classification in (
            ("foreign-enum", ForeignClassification.SECRET),
            ("plain-string", "SECRET"),
        ):
            with self.subTest(label=label):
                secret_request = ModelRequest(
                    purpose="login",
                    instruction="use the credential",
                    fields=(
                        ContextField("password", "hunter2", classification, "vault"),
                    ),
                )
                self.assertIs(
                    DataClassification.SECRET, secret_request.fields[0].classification
                )
                provider = self.build(
                    lambda request: httpx.Response(200, content=openai_body())
                )
                with self.assertRaises(DisclosureDenied):
                    await provider.complete(secret_request)
        self.assertEqual(0, len(self.requests))

    async def test_duck_typed_secret_field_fails_closed(self) -> None:
        class ForeignField:
            name = "password"
            value = "hunter2"
            classification = ForeignClassification.SECRET
            source = "vault"

        provider = self.build(lambda request: httpx.Response(200, content=openai_body()))
        with self.assertRaises(DisclosureDenied):
            await provider.complete(
                ModelRequest(
                    purpose="login",
                    instruction="use the credential",
                    fields=(ForeignField(),),
                )
            )
        self.assertEqual(0, len(self.requests))

    async def test_invalid_classification_is_rejected_at_construction(self) -> None:
        with self.assertRaises(ValidationError):
            ContextField("password", "hunter2", "TOP_SECRET", "vault")

    async def test_credential_failure_leaves_no_request_fields_in_traceback_locals(
        self,
    ) -> None:
        class ExplodingStore:
            async def put(self, *, name: str, kind: str, value: str) -> SecretHandle:
                raise AssertionError("not used")

            async def resolve_for_broker(
                self, handle: SecretHandle, *, purpose: str
            ) -> str:
                del handle, purpose
                raise ValueError("broker unavailable")

            async def delete(self, handle: SecretHandle) -> None:
                raise AssertionError("not used")

        provider = OpenAICompatibleChatProvider(
            OpenAICompatibleConfig(
                provider_id="remote.openai",
                model_id="gpt-test-1",
                base_url="https://api.example.test/v1",
                secret_handle=self.handle,
            ),
            secret_store=ExplodingStore(),
        )
        with self.assertRaises(ModelCredentialUnavailableError) as caught:
            await provider.complete(sample_request())
        probe = frame_locals(caught.exception)
        self.assertNotIn(SENSITIVE_MARKER, probe)
        self.assertNotIn(FAKE_KEY, probe)
        self.assertNotIn("authorization", probe.lower())

    async def test_parse_failure_leaves_no_response_body_in_traceback_locals(self) -> None:
        body = json.dumps(
            {
                SENSITIVE_MARKER: SENSITIVE_MARKER,
                "choices": "not-a-list",
            }
        ).encode()
        provider = self.build(lambda request: httpx.Response(200, content=body))
        with self.assertRaises(ModelProviderProtocolError) as caught:
            await provider.complete(sample_request())
        probe = frame_locals(caught.exception)
        self.assertNotIn(SENSITIVE_MARKER, probe)

    async def test_deeply_nested_json_is_a_typed_protocol_error(self) -> None:
        body = b"[" * 20_000 + b"]" * 20_000
        provider = self.build(lambda request: httpx.Response(200, content=body))
        with self.assertRaises(ModelProviderProtocolError) as caught:
            await provider.complete(sample_request())
        error = caught.exception
        self.assertIsInstance(error, ModelProviderProtocolError)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    async def test_adapter_recipient_matches_the_sent_request(self) -> None:
        provider = self.build(
            lambda request: httpx.Response(200, content=openai_body())
        )
        self.assertEqual(
            RecipientIdentity(
                provider_id="remote.openai",
                adapter="openai_compatible",
                endpoint="https://api.example.test/v1",
                model_id="gpt-test-1",
            ),
            provider.recipient,
        )
        await provider.complete(sample_request())
        request = self.requests[0]
        self.assertEqual(provider.recipient.endpoint, "https://api.example.test/v1")
        self.assertTrue(str(request.url).startswith(provider.recipient.endpoint))
        self.assertEqual(provider.recipient.model_id, self.bodies[0]["model"])

    async def test_malformed_instruction_is_rejected_before_any_request(self) -> None:
        self.build(lambda request: httpx.Response(200, content=openai_body()))
        with self.assertRaises(ValidationError):
            ModelRequest(
                purpose="draft",
                instruction=123,  # type: ignore[arg-type]
                fields=(
                    ContextField(
                        "mail_body",
                        "hello from fixture",
                        DataClassification.SENSITIVE,
                        "mail:1",
                    ),
                ),
            )
        self.assertEqual(0, len(self.requests))

    async def test_recipient_snapshot_is_pinned_across_credential_awaits(self) -> None:
        swapped = RecipientIdentity(
            provider_id="remote.openai",
            adapter="openai_compatible",
            endpoint="https://new.example.test/v1",
            model_id="gpt-new",
        )
        started = asyncio.Event()
        release = asyncio.Event()
        holder: dict[str, OpenAICompatibleChatProvider] = {}
        requests: list[httpx.Request] = []

        class SwappingStore:
            async def put(self, *, name: str, kind: str, value: str) -> SecretHandle:
                raise AssertionError("not used")

            async def resolve_for_broker(
                self, handle: SecretHandle, *, purpose: str
            ) -> str:
                del handle, purpose
                started.set()
                await release.wait()
                holder["provider"]._recipient = swapped
                return FAKE_KEY

            async def delete(self, handle: SecretHandle) -> None:
                raise AssertionError("not used")

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=openai_body())

        provider = OpenAICompatibleChatProvider(
            OpenAICompatibleConfig(
                provider_id="remote.openai",
                model_id="gpt-test-1",
                base_url="https://api.example.test/v1",
                secret_handle=self.handle,
            ),
            secret_store=SwappingStore(),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        holder["provider"] = provider
        task = asyncio.create_task(provider.complete(sample_request()))
        await started.wait()
        release.set()
        output = await task
        self.assertEqual(1, len(requests))
        self.assertEqual(
            "https://api.example.test/v1/chat/completions", str(requests[0].url)
        )
        self.assertEqual("gpt-test-1", json.loads(requests[0].content)["model"])
        self.assertEqual("gpt-test-1", output.model_id)
        self.assertEqual("remote.openai", output.provider_id)

    async def test_malformed_field_components_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ContextField("mail_body", 123, DataClassification.SENSITIVE, "mail:1")  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            ContextField("mail_body", "value", DataClassification.SENSITIVE, "")
        for bad_fields in (None, 123):
            with self.assertRaises(ValidationError) as caught:
                ModelRequest(
                    purpose="draft",
                    instruction="i",
                    fields=bad_fields,  # type: ignore[arg-type]
                )
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)

        class ExplodingField:
            @property
            def name(self) -> str:
                raise RuntimeError("hostile property")

        with self.assertRaises(ValidationError) as caught:
            ModelRequest(
                purpose="draft",
                instruction="i",
                fields=(ExplodingField(),),
            )
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn("hostile property", frame_locals(caught.exception))

    async def test_payload_build_failure_is_typed_and_scrubbed(self) -> None:
        provider = self.build(lambda request: httpx.Response(200, content=openai_body()))

        def exploding_payload(
            request: ModelRequest, model_id: str
        ) -> dict[str, Any]:
            del request, model_id
            raise AttributeError("raw-build-marker hello from fixture")

        provider._build_payload = exploding_payload  # type: ignore[method-assign]
        with self.assertRaises(ValidationError) as caught:
            await provider.complete(sample_request())
        self.assertEqual(0, len(self.requests))
        error = caught.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertNotIn("raw-build-marker", frame_locals(error))
        self.assertNotIn(SENSITIVE_MARKER, frame_locals(error))

    async def test_aclose_is_idempotent_for_owned_client(self) -> None:
        provider = self.build(lambda request: httpx.Response(200, content=openai_body()))
        await provider.aclose()
        await provider.aclose()


class LocalAdapterTests(unittest.IsolatedAsyncioTestCase):
    def build(self, handler: Any) -> OllamaChatProvider:
        transport = httpx.MockTransport(handler)
        return OllamaChatProvider(
            OllamaConfig(
                provider_id="local.ollama",
                model_id="llama3.1:8b",
                base_url="http://127.0.0.1:11434",
            ),
            http_client=httpx.AsyncClient(transport=transport),
        )

    async def test_local_call_parses_ollama_protocol(self) -> None:
        calls: list[dict[str, Any]] = []
        urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            calls.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "model": "llama3.1:8b",
                    "message": {"role": "assistant", "content": "local answer"},
                    "done": True,
                    "prompt_eval_count": 12,
                    "eval_count": 4,
                },
            )

        provider = self.build(handler)
        self.assertFalse(provider.is_remote)
        output = await provider.complete(sample_request())
        self.assertEqual("local answer", output.text)
        self.assertEqual("local.ollama", output.provider_id)
        self.assertEqual(4, output.usage["eval_count"])
        self.assertEqual("http://127.0.0.1:11434/api/chat", urls[0])
        self.assertFalse(calls[0]["stream"])
        self.assertIn("hello from fixture", calls[0]["messages"][0]["content"])

    async def test_local_adapter_refuses_secret_fields_before_sending(self) -> None:
        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(200, json={"message": {"content": "x"}, "done": True})

        provider = self.build(handler)
        secret_request = ModelRequest(
            purpose="login",
            instruction="use the credential",
            fields=(ContextField("password", "hunter2", DataClassification.SECRET, "vault"),),
        )

        with self.assertRaises(DisclosureDenied):
            await provider.complete(secret_request)
        self.assertEqual([], sent)

    async def test_local_adapter_rejects_foreign_secret_classification(self) -> None:
        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(200, json={"message": {"content": "x"}, "done": True})

        provider = self.build(handler)
        foreign = ModelRequest(
            purpose="login",
            instruction="use the credential",
            fields=(
                ContextField("password", "hunter2", ForeignClassification.SECRET, "vault"),
            ),
        )
        with self.assertRaises(DisclosureDenied):
            await provider.complete(foreign)
        self.assertEqual([], sent)

    async def test_local_adapter_ignores_environment_proxies(self) -> None:
        probe = LoopbackProxyProbe()
        await probe.start()
        try:
            with patch.dict(os.environ, proxy_environment(probe.port), clear=False):
                provider = OllamaChatProvider(
                    OllamaConfig(
                        provider_id="local.ollama",
                        model_id="llama3.1:8b",
                        base_url="http://127.0.0.1:1",
                        timeout_seconds=1.0,
                    )
                )
                try:
                    with self.assertRaises(ModelProviderUnavailableError):
                        await provider.complete(sample_request())
                finally:
                    await provider.aclose()
        finally:
            await probe.stop()
        self.assertEqual(
            b"",
            bytes(probe.received),
            "local adapter leaked loopback content through an environment proxy",
        )

    async def test_local_endpoint_must_be_loopback(self) -> None:
        for base_url in (
            "http://10.0.0.5:11434",
            "https://models.example.test",
            "http://127.0.0.1.evil.example.test:11434",
            "http://user:pass@127.0.0.1:11434",
        ):
            with self.subTest(base_url=base_url), self.assertRaises(ConfigurationError):
                OllamaChatProvider(
                    OllamaConfig(
                        provider_id="local.ollama",
                        model_id="llama3.1:8b",
                        base_url=base_url,
                    )
                )

    async def test_local_error_paths_are_typed(self) -> None:
        for label, response in (
            ("rejected", httpx.Response(500, json={"error": "model not found"})),
            ("malformed", httpx.Response(200, content=b"not-json")),
            ("incomplete", httpx.Response(200, json={"message": {}})),
        ):
            with self.subTest(label=label):
                provider = self.build(lambda request, r=response: r)
                expected = (
                    ModelProviderRejectedError
                    if label == "rejected"
                    else ModelProviderProtocolError
                )
                with self.assertRaises(expected):
                    await provider.complete(sample_request())


if __name__ == "__main__":
    unittest.main()
