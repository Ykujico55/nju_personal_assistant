"""Real loopback HTTP streaming, isolated memory host and synthetic model wire."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
import uvicorn

from personal_assistant.admin_app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.core.models import ModelRouter
from personal_assistant.infrastructure.memory import InMemorySecretStore
from personal_assistant.infrastructure.models.openai_compatible import (
    OpenAICompatibleChatProvider,
    OpenAICompatibleConfig,
)
from personal_assistant.settings import Settings


def delta(content=None, *, stop=False):
    return (
        b"data: "
        + json.dumps(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {} if content is None else {"content": content},
                        "finish_reason": "stop" if stop else None,
                    }
                ],
                "usage": {"total_tokens": 9} if stop else None,
            }
        ).encode()
        + b"\n\n"
    )


class WorkbenchLiveStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_http_delta_precedes_model_completion_and_replay_is_readonly(self):
        gate = asyncio.Event()
        closed = asyncio.Event()
        requests = []

        class ModelWire(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield delta("first")
                await gate.wait()
                yield delta(" second")
                yield delta(stop=True)
                yield b"data: [DONE]\n\n"

            async def aclose(self):
                closed.set()

        async def model_endpoint(request):
            requests.append(request)
            return httpx.Response(
                200, stream=ModelWire(), headers={"Content-Type": "text/event-stream"}
            )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = InMemorySecretStore()
            handle = await store.put(name="synthetic", kind="model_api_key", value="test-only-key")
            with patch.dict(os.environ, {}, clear=True):
                settings = replace(
                    Settings.from_env(),
                    extension_root=root / "extensions",
                    artifact_root=root / "artifacts",
                    model_remote_base_url="https://fixture.example.test",
                    model_remote_model="fixture",
                    model_remote_secret_handle=handle.id,
                    model_disclosure_mode="automatic",
                )
            container = build_container(settings, secret_store=store)
            old_router = container.model_router
            assert old_router is not None
            await old_router.aclose()
            model_client = httpx.AsyncClient(transport=httpx.MockTransport(model_endpoint))
            provider = OpenAICompatibleChatProvider(
                OpenAICompatibleConfig(
                    provider_id=settings.model_remote_provider_id,
                    model_id="fixture",
                    base_url="https://fixture.example.test",
                    secret_handle=handle,
                    json_mode=True,
                ),
                secret_store=store,
                http_client=model_client,
            )
            container.model_router = ModelRouter(
                (provider,), disclosure=container.disclosures, audit=container.audit_writer
            )
            app = create_app(settings=settings, container=container)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.listen(16)
            sock.setblocking(False)
            server = uvicorn.Server(
                uvicorn.Config(app, log_level="critical", access_log=False, lifespan="on")
            )
            owner = asyncio.create_task(server.serve(sockets=[sock]))
            try:
                async with asyncio.timeout(10):
                    while not server.started:
                        if owner.done():
                            await owner
                            self.fail("isolated HTTP server exited before startup")
                        await asyncio.sleep(0.01)
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}",
                    trust_env=False,
                    timeout=5,
                    headers={"X-Assistant-Workbench": "1"},
                ) as client:
                    path = "/admin/v1/workbench/chat/stream"
                    command = {"message": "public streaming self-check"}
                    headers = {"Idempotency-Key": "live-stream"}
                    async with client.stream(
                        "POST", path, json=command, headers=headers
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.headers["cache-control"], "no-store")
                        lines = response.aiter_lines()
                        first = []
                        async with asyncio.timeout(3):
                            async for line in lines:
                                first.append(line)
                                if line == "":
                                    break
                        self.assertIn("event: delta", first)
                        self.assertIn("first", "".join(first))
                        self.assertFalse(gate.is_set())
                        self.assertFalse(closed.is_set())
                        records = await container.audit_writer.list_events()
                        self.assertFalse(any(x.event_type == "model.completed" for x in records))
                        state = await container.records.get(
                            "workbench-chat", "workbench-stream:live-stream"
                        )
                        self.assertEqual(state["value"]["state"], "DISPATCHING")
                        gate.set()
                        remaining = [line async for line in lines]
                        self.assertIn("event: completed", remaining)
                        self.assertIn('"answer": "first second"', "".join(remaining))
                    self.assertTrue(closed.is_set())
                    state = await container.records.get(
                        "workbench-chat", "workbench-stream:live-stream"
                    )
                    self.assertEqual(state["value"]["state"], "SUCCEEDED")
                    records = await container.audit_writer.list_events()
                    model_events = [x for x in records if x.event_type == "model.completed"]
                    self.assertEqual(len(model_events), 1)
                    self.assertEqual(model_events[0].data["usage"]["total_tokens"], 9)
                    replay = await client.post(path, json=command, headers=headers)
                    self.assertEqual(replay.status_code, 200)
                    self.assertIn("event: completed", replay.text)
                    self.assertNotIn("event: delta", replay.text)
                    self.assertEqual(len(requests), 1)
            finally:
                gate.set()
                server.should_exit = True
                try:
                    await asyncio.wait_for(owner, 10)
                finally:
                    if not owner.done():
                        owner.cancel()
                        await asyncio.gather(owner, return_exceptions=True)
                    sock.close()
                    await model_client.aclose()
            self.assertTrue(owner.done())
            with socket.socket() as probe:
                probe.settimeout(0.2)
                self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
