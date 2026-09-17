"""Local Ollama chat adapter; the endpoint must be a loopback address."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from personal_assistant.core.models.errors import ModelProviderProtocolError
from personal_assistant.core.models.provider import (
    ModelOutput,
    ModelRequest,
    RecipientIdentity,
)

from .base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_RESPONSE_BYTES,
    HttpJsonModelProvider,
    render_request_text,
)

DEFAULT_LOCAL_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class OllamaConfig:
    provider_id: str
    model_id: str
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = DEFAULT_LOCAL_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS


class OllamaChatProvider(HttpJsonModelProvider):
    """Local provider speaking the Ollama ``/api/chat`` wire format.

    The base URL is validated as a loopback endpoint at construction so a
    "local" provider can never be configured to silently ship data to a remote
    host without the disclosure-consent boundary.
    """

    is_remote = False
    endpoint_path = "/api/chat"
    adapter_name = "ollama"
    usage_keys = frozenset({"prompt_eval_count", "eval_count"})

    def __init__(
        self,
        config: OllamaConfig,
        *,
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
            allow_http=True,
            require_loopback=True,
            endpoint_field="local model",
        )

    def _build_payload(self, request: ModelRequest, model_id: str) -> dict[str, Any]:
        return {
            "model": model_id,
            "messages": [{"role": "user", "content": render_request_text(request)}],
            "stream": False,
            "options": {"num_predict": self._max_output_tokens},
        }

    def _parse_output(
        self, raw: bytes, recipient: RecipientIdentity
    ) -> ModelOutput:
        payload = self._decode_json(raw)
        if payload.get("done") is not True:
            raise ModelProviderProtocolError(
                "the local model response is incomplete", provider_id=self.provider_id
            )
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ModelProviderProtocolError(
                "the local model response has no message", provider_id=self.provider_id
            )
        text = self._require_json_text(message, "content")
        return self._output(text, payload, recipient)
