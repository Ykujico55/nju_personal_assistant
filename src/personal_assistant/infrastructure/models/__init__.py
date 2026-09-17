"""Real local and remote model adapters (protocol-driven HTTP, no vendor SDK)."""

from .base import HttpJsonModelProvider, rejection_code, render_request_text
from .ollama import OllamaChatProvider, OllamaConfig
from .openai_compatible import OpenAICompatibleChatProvider, OpenAICompatibleConfig

__all__ = [
    "HttpJsonModelProvider",
    "OllamaChatProvider",
    "OllamaConfig",
    "OpenAICompatibleChatProvider",
    "OpenAICompatibleConfig",
    "rejection_code",
    "render_request_text",
]
