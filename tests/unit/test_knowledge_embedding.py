"""F05: embedding port, loopback enforcement and local adapter protocol."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from personal_knowledge.embedding import (
    DeterministicTestEmbeddingProvider,
    EmbeddingUnavailable,
    NullEmbeddingProvider,
    OllamaEmbeddingProvider,
    build_embedding_provider,
    normalize_loopback_url,
)


class _TcpCounter:
    """Counts raw TCP connections to prove no proxy is ever contacted."""

    def __init__(self) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self.port = self._socket.getsockname()[1]
        self.connections = 0
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return
            self.connections += 1
            connection.close()

    def close(self) -> None:
        self._socket.close()


class _TrickleHandler(BaseHTTPRequestHandler):
    client_closed = threading.Event()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "1000000")
        self.end_headers()
        try:
            while True:
                self.wfile.write(b" ")
                self.wfile.flush()
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            type(self).client_closed.set()

    def log_message(self, *args: object) -> None:
        return None


class _TrickleServer:
    """Sends a response body byte-by-byte forever to test deadlines."""

    def __init__(self) -> None:
        _TrickleHandler.client_closed = threading.Event()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TrickleHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        host, port = self._server.server_address[:2]
        self.base_url = f"http://{host}:{port}"

    @property
    def client_closed(self) -> threading.Event:
        return _TrickleHandler.client_closed

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _Handler(BaseHTTPRequestHandler):
    payloads: list[dict] = []
    dim = 4
    status = 200
    body: bytes | None = None

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        type(self).payloads.append(json.loads(raw.decode("utf-8")))
        body = self.body
        if body is None:
            body = json.dumps({"embedding": [0.25] * type(self).dim}).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return None


class LoopbackUrlTests(unittest.TestCase):
    def test_loopback_urls_are_accepted(self) -> None:
        self.assertEqual(
            "http://127.0.0.1:11434",
            normalize_loopback_url("http://127.0.0.1:11434/", field_name="x"),
        )
        self.assertTrue(normalize_loopback_url("http://localhost:11434", field_name="x"))

    def test_remote_or_malformed_urls_are_rejected(self) -> None:
        for value in (
            "https://api.example.com",
            "https://localhost:11434",
            "http://localhost:invalid",
            "http://192.168.1.10:11434",
            "http://user:pass@localhost:11434",
            "http://localhost:11434?x=1",
            "ftp://localhost:11434",
            "",
        ):
            with self.assertRaises(EmbeddingUnavailable):
                normalize_loopback_url(value, field_name="x")


class ProviderSelectionTests(unittest.IsolatedAsyncioTestCase):
    def test_default_is_explicit_fts_only(self) -> None:
        self.assertIsInstance(build_embedding_provider(None), NullEmbeddingProvider)
        self.assertIsInstance(
            build_embedding_provider({"provider": "none"}), NullEmbeddingProvider
        )

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaises(EmbeddingUnavailable):
            build_embedding_provider({"provider": "remote-cloud"})

    def test_null_provider_refuses_to_embed(self) -> None:
        provider = NullEmbeddingProvider()
        self.assertEqual("disabled", "disabled" if provider.identity.dim == 0 else "enabled")

    async def test_null_provider_embed_fails_closed(self) -> None:
        with self.assertRaises(EmbeddingUnavailable):
            await NullEmbeddingProvider().embed(["text"])


class DeterministicDoubleTests(unittest.IsolatedAsyncioTestCase):
    async def test_vectors_are_deterministic_and_normalized(self) -> None:
        provider = DeterministicTestEmbeddingProvider(dim=32)
        first = (await provider.embed(["alpha beta"]))[0]
        second = (await provider.embed(["alpha beta"]))[0]
        self.assertEqual(first, second)
        self.assertEqual(32, len(first))
        norm = sum(value * value for value in first) ** 0.5
        self.assertAlmostEqual(1.0, norm, places=9)

    async def test_overlap_produces_higher_similarity(self) -> None:
        provider = DeterministicTestEmbeddingProvider(dim=64)
        query = (await provider.embed(["alpha beta"]))[0]
        near = (await provider.embed(["alpha beta gamma"]))[0]
        far = (await provider.embed(["unrelated words"]))[0]

        def similarity(left: list[float], right: list[float]) -> float:
            return sum(a * b for a, b in zip(left, right, strict=True))

        self.assertGreater(similarity(query, near), similarity(query, far))

    def test_identity_is_explicitly_a_test_double(self) -> None:
        identity = DeterministicTestEmbeddingProvider().identity
        self.assertEqual("test.fake", identity.provider)
        self.assertIn("test", identity.version)


class OllamaProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _Handler.payloads = []
        _Handler.dim = 4
        _Handler.status = 200
        _Handler.body = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    async def test_embeddings_are_requested_over_loopback(self) -> None:
        provider = OllamaEmbeddingProvider(
            base_url=self.base_url, model="nomic-embed-text", timeout_seconds=10
        )
        vectors = await provider.embed(["hello world"])
        self.assertEqual(4, len(vectors[0]))
        self.assertEqual(
            {"model": "nomic-embed-text", "prompt": "hello world"},
            _Handler.payloads[0],
        )
        self.assertEqual(4, provider.identity.dim)

    async def test_rejected_status_is_a_typed_error(self) -> None:
        _Handler.status = 500
        provider = OllamaEmbeddingProvider(
            base_url=self.base_url, model="nomic-embed-text"
        )
        with self.assertRaises(EmbeddingUnavailable) as captured:
            await provider.embed(["hello"])
        self.assertEqual("EMBEDDING_REJECTED", captured.exception.code)

    async def test_malformed_json_is_a_typed_error(self) -> None:
        _Handler.body = b"{not json"
        provider = OllamaEmbeddingProvider(base_url=self.base_url, model="m")
        with self.assertRaises(EmbeddingUnavailable) as captured:
            await provider.embed(["hello"])
        self.assertEqual("EMBEDDING_PROTOCOL_ERROR", captured.exception.code)

    async def test_truncated_content_length_is_a_typed_protocol_error(self) -> None:
        async def truncated(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 100\r\nConnection: close\r\n\r\n{}"
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(truncated, "127.0.0.1", 0)
        sockets = server.sockets or []
        self.assertTrue(sockets)
        port = sockets[0].getsockname()[1]
        provider = OllamaEmbeddingProvider(base_url=f"http://127.0.0.1:{port}", model="m")
        try:
            with self.assertRaises(EmbeddingUnavailable) as captured:
                await provider.embed(["hello"])
        finally:
            server.close()
            await server.wait_closed()
        self.assertEqual("EMBEDDING_PROTOCOL_ERROR", captured.exception.code)

    async def test_dimension_change_is_rejected(self) -> None:
        provider = OllamaEmbeddingProvider(base_url=self.base_url, model="m")
        await provider.embed(["first"])
        _Handler.dim = 6
        with self.assertRaises(EmbeddingUnavailable) as captured:
            await provider.embed(["second"])
        self.assertEqual("EMBEDDING_DIMENSION_CHANGED", captured.exception.code)

    async def test_non_numeric_vector_is_rejected(self) -> None:
        _Handler.body = json.dumps({"embedding": ["x", 1.0]}).encode("utf-8")
        provider = OllamaEmbeddingProvider(base_url=self.base_url, model="m")
        with self.assertRaises(EmbeddingUnavailable):
            await provider.embed(["hello"])

    async def test_environment_proxies_are_never_consulted(self) -> None:
        import os
        from unittest.mock import patch

        counter = _TcpCounter()
        counter.start()
        proxy_url = f"http://127.0.0.1:{counter.port}"
        provider = OllamaEmbeddingProvider(
            base_url="http://127.0.0.1:1", model="m", timeout_seconds=2
        )
        try:
            with patch.dict(
                os.environ,
                {
                    "HTTP_PROXY": proxy_url,
                    "HTTPS_PROXY": proxy_url,
                    "ALL_PROXY": proxy_url,
                    "http_proxy": proxy_url,
                    "all_proxy": proxy_url,
                },
            ), self.assertRaises(EmbeddingUnavailable):
                await provider.embed(["personal secret text"])
        finally:
            counter.close()
        self.assertEqual(0, counter.connections, "no traffic may reach an env proxy")

    async def test_slow_trickle_respects_the_total_deadline(self) -> None:
        trickle = _TrickleServer()
        trickle.start()
        provider = OllamaEmbeddingProvider(
            base_url=trickle.base_url, model="m", timeout_seconds=1.0
        )
        started = time.monotonic()
        try:
            with self.assertRaises(EmbeddingUnavailable) as captured:
                await provider.embed(["slow"])
            elapsed = time.monotonic() - started
        finally:
            trickle.close()
        self.assertEqual("EMBEDDING_TIMEOUT", captured.exception.code)
        self.assertLess(elapsed, 2.5, f"total deadline was not enforced ({elapsed:.2f}s)")

    async def test_cancellation_closes_the_socket(self) -> None:
        trickle = _TrickleServer()
        trickle.start()
        provider = OllamaEmbeddingProvider(
            base_url=trickle.base_url, model="m", timeout_seconds=30.0
        )
        task = asyncio.create_task(provider.embed(["cancel me"]))
        try:
            await asyncio.sleep(0.5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(
                trickle.client_closed.wait(5.0),
                "the connection must be closed when the caller cancels",
            )
        finally:
            trickle.close()


if __name__ == "__main__":
    unittest.main()
