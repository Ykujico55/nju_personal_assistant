"""OpenAI-compatible remote chat adapter (protocol only, no vendor SDK)."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import httpx

from personal_assistant.core.models.cleanup import run_cleanup
from personal_assistant.core.models.errors import (
    ModelCredentialUnavailableError,
    ModelOutputFilteredError,
    ModelOutputTruncatedError,
    ModelProviderProtocolError,
    ModelProviderTimeoutError,
)
from personal_assistant.core.models.provider import (
    ModelOutput,
    ModelRequest,
    ModelStreamEvent,
    RecipientIdentity,
    is_secret_classification,
)
from personal_assistant.core.models.router import DisclosureDenied
from personal_assistant.core.secrets import SecretHandle, SecretStorePort
from personal_assistant.settings import ConfigurationError

from .base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    HttpJsonModelProvider,
    render_request_text,
)
from .streaming import response_lines

# Bearer credentials are bounded to visible ASCII with no whitespace or control
# characters, so they survive header encoding unchanged and never trigger a
# ``UnicodeEncodeError`` that could retain the raw credential.
_CREDENTIAL = re.compile(r"\A[\x21-\x7e]{1,4096}\Z")


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    provider_id: str
    model_id: str
    base_url: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_output_tokens: int | None = DEFAULT_MAX_OUTPUT_TOKENS
    secret_handle: SecretHandle | None = None
    json_mode: bool = False
    thinking: str | None = None


class OpenAICompatibleChatProvider(HttpJsonModelProvider):
    """Remote provider speaking the OpenAI chat-completions wire format.

    The API key is resolved from the host broker through a ``SecretHandle`` on
    every call.  Resolution failure fails closed before any request is sent and
    the handle is never serialized into the request body.
    """

    is_remote = True
    endpoint_path = "/chat/completions"
    adapter_name = "openai_compatible"
    usage_keys = frozenset({"prompt_tokens", "completion_tokens", "total_tokens"})

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        secret_store: SecretStorePort | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            provider_id=config.provider_id,
            model_id=config.model_id,
            base_url=config.base_url,
            timeout_seconds=config.timeout_seconds,
            max_response_bytes=config.max_response_bytes,
            max_output_tokens=config.max_output_tokens,
            http_client=http_client,
            endpoint_field="remote model",
        )
        if config.secret_handle is None:
            raise ConfigurationError("remote model requires a SecretHandle-backed credential")
        if secret_store is None:
            raise ConfigurationError("remote model requires a host credential store")
        if type(config.json_mode) is not bool or config.thinking not in {
            None,
            "enabled",
            "disabled",
        }:
            raise ConfigurationError("invalid model response options")
        self._json_mode, self._thinking = config.json_mode, config.thinking
        self._secret_handle = config.secret_handle
        self._secret_store = secret_store

    async def _headers(self) -> dict[str, str]:
        api_key = await self._resolve_credential()
        return {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}

    async def _resolve_credential(self) -> str:
        credential: object = None
        try:
            credential = await self._secret_store.resolve_for_broker(
                self._secret_handle, purpose=f"model:{self.provider_id}:complete"
            )
        except Exception:
            # Deliberately drop the broker failure and its message: a broker
            # error may embed credential material, and chaining it would make a
            # formatted traceback render it.  Failure stays fail closed.
            credential = None
        # Bound the credential to visible ASCII before it can reach an HTTP
        # header: a non-ASCII value would otherwise raise a bare
        # ``UnicodeEncodeError`` whose ``object`` holds the whole credential.
        if not isinstance(credential, str) or not _CREDENTIAL.fullmatch(credential):
            del credential
            raise ModelCredentialUnavailableError(
                "model credentials are unavailable", provider_id=self.provider_id
            )
        return credential

    def _build_payload(self, request: ModelRequest, model_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "user", "content": render_request_text(request)}],
            "stream": False,
        }
        if self._max_output_tokens is not None:
            payload["max_tokens"] = self._max_output_tokens
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self._thinking is not None:
            payload["thinking"] = {"type": self._thinking}
        return payload

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelStreamEvent, None]:
        recipient = self._recipient
        if any(is_secret_classification(field.classification) for field in request.fields):
            raise DisclosureDenied("secret material may never be sent to a model")
        deadline = asyncio.get_running_loop().time() + self._timeout_seconds
        try:
            headers = await asyncio.wait_for(self._headers(), self._timeout_seconds)
        except TimeoutError:
            raise ModelProviderTimeoutError(
                "model credentials timed out", provider_id=self.provider_id
            ) from None
        payload = self._build_payload(request, recipient.model_id)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        if request.metadata.get("response_format") == "text":
            payload.pop("response_format", None)
        headers["Accept"] = "text/event-stream"
        lines = response_lines(self, payload, headers, recipient, deadline=deadline)
        data: list[str] = []
        text: list[str] = []
        usage: Any = None
        finished = False
        done = False
        try:
            async for line in lines:
                if line.startswith("data:"):
                    data.append(line[5:].removeprefix(" "))
                    continue
                if line or not data:
                    continue
                event = "\n".join(data)
                data.clear()
                if event == "[DONE]":
                    done = True
                    break
                try:
                    value = json.loads(event)
                except ValueError:
                    value = None
                if not isinstance(value, dict) or "error" in value:
                    raise self._stream_protocol_error()
                if value.get("usage") is not None:
                    usage = value["usage"]
                choices = value.get("choices")
                # OpenAI may send a separate usage-only chunk after finish.
                if choices == [] and finished and isinstance(value.get("usage"), dict):
                    continue
                if not isinstance(choices, list) or len(choices) != 1:
                    raise self._stream_protocol_error()
                choice = choices[0]
                if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                    raise self._stream_protocol_error()
                delta = choice.get("delta")
                if (
                    not isinstance(delta, dict)
                    or delta.get("tool_calls")
                    or delta.get("function_call")
                ):
                    raise self._stream_protocol_error()
                content = delta.get("content")
                if content is not None:
                    if not isinstance(content, str) or finished:
                        raise self._stream_protocol_error()
                    valid_utf8 = True
                    try:
                        content.encode("utf-8")
                    except UnicodeError:
                        valid_utf8 = False
                    if not valid_utf8:
                        raise self._stream_protocol_error()
                    if content:
                        text.append(content)
                        yield ModelStreamEvent(delta=content)
                reason = choice.get("finish_reason")
                if reason is not None:
                    if finished:
                        raise self._stream_protocol_error()
                    self._check_termination(reason)
                    if reason != "stop":
                        raise self._stream_protocol_error()
                    finished = True
        finally:
            await run_cleanup(lines.aclose())
            headers.clear()
            payload.clear()
        answer = "".join(text)
        if not done or not finished or not answer.strip():
            raise self._stream_protocol_error()
        yield ModelStreamEvent(output=self._output(answer, usage, recipient))

    def _check_termination(self, reason: object) -> None:
        if reason == "length":
            raise ModelOutputTruncatedError(
                "the model ended at its output limit", provider_id=self.provider_id
            )
        if reason == "content_filter":
            raise ModelOutputFilteredError(
                "the model ended with content filtering", provider_id=self.provider_id
            )

    def _stream_protocol_error(self) -> ModelProviderProtocolError:
        return ModelProviderProtocolError(
            "the model stream is malformed or incomplete", provider_id=self.provider_id
        )

    def _parse_output(self, raw: bytes, recipient: RecipientIdentity) -> ModelOutput:
        payload = self._decode_json(raw)
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelProviderProtocolError(
                "the model response has no choices", provider_id=self.provider_id
            )
        first = choices[0]
        if not isinstance(first, dict):
            raise ModelProviderProtocolError(
                "the model response choice is malformed", provider_id=self.provider_id
            )
        self._check_termination(first.get("finish_reason"))
        message = first.get("message")
        if not isinstance(message, dict):
            raise ModelProviderProtocolError(
                "the model response has no message", provider_id=self.provider_id
            )
        text = self._require_json_text(message, "content")
        return self._output(text, payload.get("usage"), recipient)
