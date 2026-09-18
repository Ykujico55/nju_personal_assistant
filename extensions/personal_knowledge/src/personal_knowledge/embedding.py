"""Replaceable embedding port with an explicit, local-only production adapter.

The extension never sends personal text to a remote embedding service.  The
default configuration (``provider = "none"``) degrades explicitly to FTS-only
retrieval: vector rows stay NULL and search reports ``vector_mode: disabled``.
``OllamaEmbeddingProvider`` talks to a loopback HTTP endpoint only.

``DeterministicTestEmbeddingProvider`` is a lexical hash double used by tests.
It is not a semantic model and must never be described as production embedding.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

MAX_EMBEDDING_TEXTS = 512
MAX_EMBEDDING_DIM = 8192
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class EmbeddingUnavailable(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class EmbeddingIdentity:
    provider: str
    model: str
    dim: int
    version: str

    def key(self) -> str:
        return f"{self.provider}|{self.model}|{self.dim}|{self.version}"


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def identity(self) -> EmbeddingIdentity: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def aclose(self) -> None: ...


class NullEmbeddingProvider:
    """Explicit FTS-only degradation; no vectors are ever produced."""

    def __init__(self) -> None:
        self._identity = EmbeddingIdentity(
            provider="none", model="", dim=0, version="none"
        )

    @property
    def identity(self) -> EmbeddingIdentity:
        return self._identity

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        del texts
        raise EmbeddingUnavailable(
            "EMBEDDING_DISABLED", "no embedding provider is configured"
        )

    async def aclose(self) -> None:
        return None


class DeterministicTestEmbeddingProvider:
    """Test-only lexical hashing double (not a semantic embedding model)."""

    def __init__(self, *, dim: int = 64, model: str = "lexical-hash-double") -> None:
        if not 8 <= dim <= MAX_EMBEDDING_DIM:
            raise ValueError("dim must be between 8 and 8192")
        self._identity = EmbeddingIdentity(
            provider="test.fake", model=model, dim=dim, version="test-double-1"
        )

    @property
    def identity(self) -> EmbeddingIdentity:
        return self._identity

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if len(texts) > MAX_EMBEDDING_TEXTS:
            raise EmbeddingUnavailable("EMBEDDING_TOO_MANY", "too many texts in one batch")
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        dim = self._identity.dim
        vector = [0.0] * dim
        for token in re.findall(r"[\w\u4e00-\u9fff]+", text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % dim
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]

    async def aclose(self) -> None:
        return None


class OllamaEmbeddingProvider:
    """Loopback-only Ollama embeddings adapter (stdlib async, bounded, no retry).

    The request is written directly over ``asyncio.open_connection``: environment
    proxies are never consulted, redirects are never followed, one monotonic
    deadline covers connect + write + full response, and cancellation closes the
    socket immediately (no background thread keeps transferring personal text).
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = normalize_loopback_url(base_url, field_name="embedding.base_url")
        if not isinstance(model, str) or not model.strip() or len(model) > 128:
            raise EmbeddingUnavailable("EMBEDDING_CONFIG_INVALID", "embedding model is invalid")
        if not 1.0 <= timeout_seconds <= 120.0:
            raise EmbeddingUnavailable(
                "EMBEDDING_CONFIG_INVALID", "embedding timeout is out of range"
            )
        self._model = model.strip()
        self._timeout = float(timeout_seconds)
        self._dim = 0

    @property
    def identity(self) -> EmbeddingIdentity:
        return EmbeddingIdentity(
            provider="ollama",
            model=self._model,
            dim=self._dim,
            version=f"ollama:{self._model}:v1",
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if len(texts) > MAX_EMBEDDING_TEXTS:
            raise EmbeddingUnavailable("EMBEDDING_TOO_MANY", "too many texts in one batch")
        vectors: list[list[float]] = []
        for text in texts:
            vector = await self._embed_one(text)
            if self._dim == 0:
                self._dim = len(vector)
            elif len(vector) != self._dim:
                raise EmbeddingUnavailable(
                    "EMBEDDING_DIMENSION_CHANGED",
                    "the embedding model changed dimension; rebuild the index",
                )
            vectors.append(vector)
        return vectors

    async def _embed_one(self, text: str) -> list[float]:
        payload = json.dumps({"model": self._model, "prompt": text}).encode("utf-8")
        raw = await _post_json(
            self._base_url,
            "/api/embeddings",
            payload,
            timeout_seconds=self._timeout,
            max_bytes=MAX_RESPONSE_BYTES,
        )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise EmbeddingUnavailable(
                "EMBEDDING_PROTOCOL_ERROR", "embedding response is not valid JSON"
            ) from exc
        embedding = body.get("embedding") if isinstance(body, dict) else None
        if not isinstance(embedding, list) or not embedding:
            raise EmbeddingUnavailable(
                "EMBEDDING_PROTOCOL_ERROR", "embedding response has no vector"
            )
        if len(embedding) > MAX_EMBEDDING_DIM:
            raise EmbeddingUnavailable("EMBEDDING_TOO_LARGE", "embedding dimension is too large")
        vector: list[float] = []
        for value in embedding:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EmbeddingUnavailable(
                    "EMBEDDING_PROTOCOL_ERROR", "embedding contains non-numeric values"
                )
            number = float(value)
            if not math.isfinite(number):
                raise EmbeddingUnavailable(
                    "EMBEDDING_PROTOCOL_ERROR", "embedding contains non-finite values"
                )
            vector.append(number)
        return vector

    async def aclose(self) -> None:
        return None


_MAX_HEADER_BYTES = 16 * 1024


async def _post_json(
    base_url: str,
    path: str,
    payload: bytes,
    *,
    timeout_seconds: float,
    max_bytes: int,
) -> bytes:
    parts = urllib.parse.urlsplit(base_url)
    host = parts.hostname or ""
    port = parts.port or 80
    request_path = f"{parts.path.rstrip('/')}{path}"
    host_header = parts.netloc
    try:
        async with asyncio.timeout(timeout_seconds):
            reader, writer = await asyncio.open_connection(host, port)
            try:
                head = (
                    f"POST {request_path} HTTP/1.1\r\n"
                    f"Host: {host_header}\r\n"
                    "Accept: application/json\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(payload)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                writer.write(head + payload)
                await writer.drain()
                return await _read_http_body(reader, max_bytes=max_bytes)
            finally:
                # Synchronous close is cancellation-safe and never leaves the
                # stream half-open behind a cancelled task.
                writer.close()
    except TimeoutError as exc:
        raise EmbeddingUnavailable(
            "EMBEDDING_TIMEOUT", "the local embedding endpoint exceeded its deadline"
        ) from exc
    except EmbeddingUnavailable:
        raise
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        raise EmbeddingUnavailable(
            "EMBEDDING_PROTOCOL_ERROR", "the local embedding response was truncated"
        ) from exc
    except (OSError, ValueError) as exc:
        raise EmbeddingUnavailable(
            "EMBEDDING_UNAVAILABLE", "the local embedding endpoint is unreachable"
        ) from exc


async def _read_http_body(reader: asyncio.StreamReader, *, max_bytes: int) -> bytes:
    status_line = await reader.readline()
    if not status_line:
        raise EmbeddingUnavailable(
            "EMBEDDING_PROTOCOL_ERROR", "embedding endpoint closed without a response"
        )
    if len(status_line) > _MAX_HEADER_BYTES:
        raise EmbeddingUnavailable("EMBEDDING_PROTOCOL_ERROR", "malformed status line")
    fields = status_line.decode("latin-1").split(" ", 2)
    if len(fields) < 2 or not fields[1].isdigit():
        raise EmbeddingUnavailable("EMBEDDING_PROTOCOL_ERROR", "malformed status line")
    status = int(fields[1])
    headers: dict[str, str] = {}
    total_header = len(status_line)
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        total_header += len(line)
        if total_header > _MAX_HEADER_BYTES:
            raise EmbeddingUnavailable("EMBEDDING_PROTOCOL_ERROR", "response headers are too large")
        name, _, value = line.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip()
    if status != 200:
        raise EmbeddingUnavailable(
            "EMBEDDING_REJECTED", "the embedding endpoint rejected the request"
        )
    transfer_encoding = headers.get("transfer-encoding", "").lower()
    if "chunked" in transfer_encoding:
        return await _read_chunked(reader, max_bytes=max_bytes)
    length_header = headers.get("content-length")
    if length_header is not None:
        if not length_header.isdigit():
            raise EmbeddingUnavailable("EMBEDDING_PROTOCOL_ERROR", "invalid content-length")
        length = int(length_header)
        if length > max_bytes:
            raise EmbeddingUnavailable("EMBEDDING_TOO_LARGE", "embedding response is too large")
        return await reader.readexactly(length)
    body = await reader.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise EmbeddingUnavailable("EMBEDDING_TOO_LARGE", "embedding response is too large")
    return body


async def _read_chunked(reader: asyncio.StreamReader, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        size_line = await reader.readline()
        if not size_line or len(size_line) > _MAX_HEADER_BYTES:
            raise EmbeddingUnavailable("EMBEDDING_PROTOCOL_ERROR", "malformed chunked response")
        token = size_line.split(b";", 1)[0].strip()
        try:
            size = int(token, 16)
        except ValueError as exc:
            raise EmbeddingUnavailable(
                "EMBEDDING_PROTOCOL_ERROR", "malformed chunk size"
            ) from exc
        if size == 0:
            return b"".join(chunks)
        total += size
        if total > max_bytes:
            raise EmbeddingUnavailable("EMBEDDING_TOO_LARGE", "embedding response is too large")
        chunks.append(await reader.readexactly(size))
        await reader.readline()


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "[::1]"})


def normalize_loopback_url(raw: str, *, field_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise EmbeddingUnavailable("EMBEDDING_CONFIG_INVALID", f"{field_name} is required")
    value = raw.strip()
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "http":
        raise EmbeddingUnavailable(
            "EMBEDDING_CONFIG_INVALID", f"{field_name} must use loopback http"
        )
    if parts.username or parts.password or parts.query or parts.fragment:
        raise EmbeddingUnavailable(
            "EMBEDDING_CONFIG_INVALID",
            f"{field_name} may not contain credentials, query or fragment",
        )
    host = parts.hostname or ""
    if host.lower() not in _LOOPBACK_HOSTS:
        raise EmbeddingUnavailable(
            "EMBEDDING_NOT_LOCAL",
            f"{field_name} must point at a loopback address; personal text is never sent remotely",
        )
    try:
        port = parts.port
    except ValueError as exc:
        raise EmbeddingUnavailable(
            "EMBEDDING_CONFIG_INVALID", f"{field_name} has an invalid port"
        ) from exc
    if port is not None and not 1 <= port <= 65535:
        raise EmbeddingUnavailable(
            "EMBEDDING_CONFIG_INVALID", f"{field_name} has an invalid port"
        )
    path = parts.path.rstrip("/")
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise EmbeddingUnavailable("EMBEDDING_CONFIG_INVALID", f"{field_name} path is invalid")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def build_embedding_provider(config: object) -> EmbeddingProvider:
    """Build the configured provider; default is an explicit FTS-only fallback."""

    if config is None:
        return NullEmbeddingProvider()
    if not isinstance(config, dict):
        raise EmbeddingUnavailable("EMBEDDING_CONFIG_INVALID", "embedding config must be an object")
    provider = config.get("provider", "none")
    if provider == "none":
        return NullEmbeddingProvider()
    if provider == "ollama":
        base_url = config.get("base_url")
        model = config.get("model")
        if not isinstance(base_url, str) or not isinstance(model, str):
            raise EmbeddingUnavailable(
                "EMBEDDING_CONFIG_INVALID", "ollama embeddings require base_url and model"
            )
        timeout = config.get("timeout_seconds", 30.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise EmbeddingUnavailable(
                "EMBEDDING_CONFIG_INVALID", "embedding timeout must be a number"
            )
        return OllamaEmbeddingProvider(
            base_url=base_url, model=model, timeout_seconds=float(timeout)
        )
    raise EmbeddingUnavailable(
        "EMBEDDING_CONFIG_INVALID", f"unsupported embedding provider: {provider}"
    )


__all__ = [
    "DeterministicTestEmbeddingProvider",
    "EmbeddingIdentity",
    "EmbeddingProvider",
    "EmbeddingUnavailable",
    "NullEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "build_embedding_provider",
    "normalize_loopback_url",
]
