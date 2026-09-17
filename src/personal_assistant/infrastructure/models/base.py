"""Protocol-driven HTTP transport shared by the F04 model adapters.

Adapters in this package talk plain JSON over HTTPS to a configured endpoint;
no vendor SDK is imported anywhere.  They never log or persist prompts, field
values, credentials or raw provider bodies, never retry silently and translate
every transport failure into the typed errors in ``core.models.errors``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from personal_assistant.core.models.cleanup import run_cleanup
from personal_assistant.core.models.errors import (
    ModelCredentialUnavailableError,
    ModelProviderProtocolError,
    ModelProviderRejectedError,
    ModelProviderResponseTooLargeError,
    ModelProviderTimeoutError,
    ModelProviderUnavailableError,
)
from personal_assistant.core.models.provider import (
    ModelOutput,
    ModelRequest,
    RecipientIdentity,
    bounded_usage,
    is_secret_classification,
)
from personal_assistant.core.models.router import DisclosureDenied
from personal_assistant.domain import ValidationError
from personal_assistant.settings import (
    ConfigurationError,
    normalize_model_endpoint,
    normalize_model_id,
    normalize_provider_id,
)

DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
MIN_MAX_RESPONSE_BYTES = 1024
MAX_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_OUTPUT_TOKENS = 1024
MAX_OUTPUT_TOKENS = 32_768

# A fixed, adapter-owned classification for a non-success status.  The vendor
# response body is never parsed for this: any string a provider controls could
# carry credential or field-value material into an exception.
_REJECTION_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    408: "request_timeout",
    409: "conflict",
    413: "payload_too_large",
    422: "unprocessable_entity",
    429: "rate_limited",
    500: "server_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}


def rejection_code(status: int) -> str | None:
    """Map an HTTP status to a fixed enum; never echo provider text."""

    return _REJECTION_CODES.get(status)


def render_request_text(request: ModelRequest) -> str:
    """Render instruction plus classified fields for a single user message.

    Classification and source are preserved so the model can reason about the
    data boundary.  The result is only ever placed in the outbound HTTP body.
    """

    sections = [request.instruction.strip()]
    if request.fields:
        sections.append(
            "\n".join(
                f"[{field.classification.value}] {field.name} ({field.source}): {field.value}"
                for field in request.fields
            )
        )
    return "\n\n".join(section for section in sections if section)


class HttpJsonModelProvider:
    """Base class for JSON-over-HTTP chat providers."""

    is_remote: bool = False
    endpoint_path: str = ""
    adapter_name: str = ""
    # Only the adapter's own key set may survive into ``ModelOutput.usage``;
    # a provider cannot smuggle a sensitive marker in as a usage key.
    usage_keys: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        http_client: httpx.AsyncClient | None = None,
        allow_http: bool = False,
        require_loopback: bool = False,
        endpoint_field: str = "model base url",
    ) -> None:
        normalized_provider_id = normalize_provider_id(
            provider_id, field_name=f"{endpoint_field}: provider_id"
        )
        normalized_model_id = normalize_model_id(
            model_id, field_name=f"{endpoint_field}: model_id"
        )
        normalized_base_url = normalize_model_endpoint(
            base_url,
            field_name=f"{endpoint_field}: base_url",
            allow_http=allow_http,
            require_loopback=require_loopback,
        )
        if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ConfigurationError(
                f"{endpoint_field}: timeout must be between 0 and {MAX_TIMEOUT_SECONDS:g} seconds"
            )
        if not MIN_MAX_RESPONSE_BYTES <= max_response_bytes <= MAX_MAX_RESPONSE_BYTES:
            raise ConfigurationError(
                f"{endpoint_field}: response limit must be between "
                f"{MIN_MAX_RESPONSE_BYTES} and {MAX_MAX_RESPONSE_BYTES} bytes"
            )
        if not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise ConfigurationError(
                f"{endpoint_field}: max output tokens must be between 1 and {MAX_OUTPUT_TOKENS}"
            )
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_output_tokens = max_output_tokens
        self._client = http_client
        self._owns_client = http_client is None
        self._client_lock = asyncio.Lock()
        # One frozen identity is the single source of truth for the outbound
        # URL, the model id sent in the payload and the recipient a disclosure
        # consent is bound to.  Repointing an endpoint requires a new provider
        # (and therefore a new router registration), never a runtime mutation.
        self._recipient = RecipientIdentity(
            provider_id=normalized_provider_id,
            adapter=self.adapter_name,
            endpoint=normalized_base_url,
            model_id=normalized_model_id,
        )

    @property
    def provider_id(self) -> str:
        return self._recipient.provider_id

    @property
    def model_id(self) -> str:
        return self._recipient.model_id

    @property
    def recipient(self) -> RecipientIdentity:
        return self._recipient

    async def complete(self, request: ModelRequest) -> ModelOutput:
        # Capture one request-scoped identity before any ``await``: replacing
        # ``_recipient`` while credentials or the transport are pending can
        # never change where this request is sent or which model it is
        # recorded as.
        recipient = self._recipient
        # Defence in depth: even a direct adapter call must never transmit
        # SECRET material, independent of any router-level check.  An unknown
        # classification counts as SECRET (fail closed).
        if any(
            is_secret_classification(field.classification)
            for field in request.fields
        ):
            del request, recipient
            raise DisclosureDenied("secret material may never be sent to a model")
        payload: dict[str, Any] = {}
        headers: dict[str, str] = {}
        raw = b""
        output: ModelOutput | None = None
        malformed_request = False
        malformed_response = False
        cancelled = False
        try:
            headers = await self._headers()
            try:
                payload = self._build_payload(request, recipient.model_id)
            except Exception:  # noqa: BLE001 - malformed request, fail closed
                # Rendering or payload assembly failed (e.g. a hand-crafted
                # request that slipped past validation).  The original
                # exception keeps the request fields in its frames, so it is
                # discarded and a typed error is raised from this cleaned frame.
                malformed_request = True
            else:
                raw = await self._post_json(
                    payload, headers=headers, recipient=recipient
                )
        finally:
            # Credential resolution, payload construction and transport all
            # happen under this boundary: a failure must not leave the request
            # fields, credentials or serialized body in a traceback frame.
            del request, payload, headers
        if malformed_request:
            raise ValidationError("the model request could not be rendered")
        try:
            output = self._parse_output(raw, recipient)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 - malformed or over-deep responses
            # ``RecursionError`` from a hostile deep JSON document and any
            # other parse failure become one typed protocol error, raised
            # below from a frame that no longer holds the raw body.
            malformed_response = True
        finally:
            del raw
        if cancelled:
            raise asyncio.CancelledError()
        if malformed_response or output is None:
            raise ModelProviderProtocolError(
                "the model response is malformed", provider_id=self.provider_id
            )
        return output

    async def aclose(self) -> None:
        async with self._client_lock:
            client = self._client
            owned = self._owns_client
            self._client = None
            self._owns_client = False
        if client is not None and owned:
            # A second cancellation must not interrupt the transport shutdown.
            await run_cleanup(client.aclose())

    # -- hooks ---------------------------------------------------------------

    async def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    def _build_payload(self, request: ModelRequest, model_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def _parse_output(self, raw: bytes, recipient: RecipientIdentity) -> ModelOutput:
        raise NotImplementedError

    # -- transport -----------------------------------------------------------

    async def _post_json(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str],
        recipient: RecipientIdentity,
    ) -> bytes:
        url = f"{recipient.endpoint}{self.endpoint_path}"
        request_headers = {**headers, "Content-Type": "application/json"}
        client: httpx.AsyncClient | None = None
        request: httpx.Request | None = None
        response: httpx.Response | None = None
        failure: str | None = None
        cancelled = False
        close_error: BaseException | None = None
        body = b""
        status = 0
        try:
            client = await self._ensure_client()
            request = client.build_request(
                "POST", url, json=payload, headers=request_headers
            )
            # One monotonic deadline bounds the whole exchange; httpx read
            # timeouts only bound individual chunks.
            async with asyncio.timeout(self._timeout_seconds):
                # Redirects stay disabled per request so credentials can never
                # be forwarded to another host, even with an injected client
                # that enables redirects by default.
                response = await client.send(
                    request, stream=True, follow_redirects=False
                )
                body = await self._read_bounded(response)
                status = response.status_code
        except asyncio.CancelledError:
            # Cleanup still runs; the cancellation is re-raised with priority
            # over any close failure below.
            cancelled = True
        except TimeoutError:
            failure = "timeout"
        except httpx.TimeoutException:
            failure = "timeout"
        except ModelProviderResponseTooLargeError:
            failure = "too_large"
        except httpx.HTTPError:
            failure = "unavailable"
        except Exception:  # noqa: BLE001 - any transport-level failure, fail closed
            # Stream shutdown (including a failing ``aclose``) can surface from
            # inside the body iteration as an arbitrary exception.  It is still
            # a transport failure and must not escape untranslated.
            failure = "unavailable"
        finally:
            if response is not None:
                # Shielded so a repeated cancellation cannot interrupt the
                # stream close.  A close failure must never mask a cancellation
                # or the original typed error, so it is recorded separately.
                try:
                    await run_cleanup(response.aclose())
                except asyncio.CancelledError:
                    cancelled = True
                except BaseException as exc:  # noqa: BLE001 - reported last
                    close_error = exc
        if isinstance(close_error, asyncio.CancelledError):
            cancelled = True
        rejected = not 200 <= status < 300
        code = rejection_code(status)
        status_code = status
        if cancelled or failure is not None or rejected or close_error is not None:
            # Drop every sensitive reference (headers carry the credential and
            # the payload is the serialized request body) before constructing
            # an error in this frame, so an error reporter reading frame locals
            # cannot recover them.
            del payload, headers, url, request_headers, client, request
            del response, body, close_error
            if cancelled:
                raise asyncio.CancelledError()
            if failure == "timeout":
                raise ModelProviderTimeoutError(
                    "the model endpoint timed out", provider_id=self.provider_id
                )
            if failure == "too_large":
                raise ModelProviderResponseTooLargeError(
                    "the model response exceeded the size limit",
                    provider_id=self.provider_id,
                )
            if failure == "unavailable":
                raise ModelProviderUnavailableError(
                    "the model endpoint is unreachable", provider_id=self.provider_id
                )
            if rejected:
                raise ModelProviderRejectedError(
                    "the model endpoint returned a non-success status",
                    provider_id=self.provider_id,
                    status_code=status_code,
                    rejection_code=code,
                )
            # A close-only failure: convert it to a safe, unchained typed error
            # instead of surfacing the raw transport exception.
            raise ModelProviderUnavailableError(
                "the model transport could not be closed cleanly",
                provider_id=self.provider_id,
            )
        return body

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > self._max_response_bytes:
                raise ModelProviderResponseTooLargeError(
                    "the model response exceeded the size limit",
                    provider_id=self.provider_id,
                )
        return bytes(body)

    async def _ensure_client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            client = self._client
            if client is None:
                client = httpx.AsyncClient(
                    timeout=httpx.Timeout(self._timeout_seconds),
                    follow_redirects=False,
                    trust_env=False,
                    headers={"Accept": "application/json"},
                )
                self._client = client
                self._owns_client = True
            return client

    def _decode_json(self, raw: bytes) -> dict[str, Any]:
        payload: Any = None
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            # ``JSONDecodeError`` keeps the raw document on the exception;
            # dropping it here prevents a response body from surviving in an
            # exception chain.
            payload = None
        if not isinstance(payload, dict):
            raise ModelProviderProtocolError(
                "the model response is not valid JSON", provider_id=self.provider_id
            )
        return payload

    def _require_json_text(self, payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ModelProviderProtocolError(
                "the model response has no completion text", provider_id=self.provider_id
            )
        return value

    def _output(
        self, text: str, usage: Any, recipient: RecipientIdentity
    ) -> ModelOutput:
        # Identity always comes from the request-scoped recipient snapshot: a
        # provider-controlled response field must never reach an audit record
        # as an identity, and a concurrent re-point must not change it.
        return ModelOutput(
            text=text,
            provider_id=recipient.provider_id,
            model_id=recipient.model_id,
            usage=bounded_usage(usage, self.usage_keys),
        )


__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "HttpJsonModelProvider",
    "ModelCredentialUnavailableError",
    "rejection_code",
    "render_request_text",
]
