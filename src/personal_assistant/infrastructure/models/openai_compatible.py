"""OpenAI-compatible remote chat adapter (protocol only, no vendor SDK)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from personal_assistant.core.models.errors import (
    ModelCredentialUnavailableError,
    ModelProviderProtocolError,
)
from personal_assistant.core.models.provider import (
    ModelOutput,
    ModelRequest,
    RecipientIdentity,
)
from personal_assistant.core.secrets import SecretHandle, SecretStorePort
from personal_assistant.settings import ConfigurationError

from .base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    HttpJsonModelProvider,
    render_request_text,
)

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
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    secret_handle: SecretHandle | None = None


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
        return {
            "model": model_id,
            "messages": [{"role": "user", "content": render_request_text(request)}],
            "stream": False,
            "max_tokens": self._max_output_tokens,
        }

    def _parse_output(
        self, raw: bytes, recipient: RecipientIdentity
    ) -> ModelOutput:
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
        message = first.get("message")
        if not isinstance(message, dict):
            raise ModelProviderProtocolError(
                "the model response has no message", provider_id=self.provider_id
            )
        text = self._require_json_text(message, "content")
        return self._output(text, payload.get("usage"), recipient)
