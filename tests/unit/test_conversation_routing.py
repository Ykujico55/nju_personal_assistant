from __future__ import annotations

import asyncio
import json
import unittest

from personal_assistant.api.workbench_conversation import bounded_stream
from personal_assistant.core.conversation import parse_route, routing_request, task_objective
from personal_assistant.domain import ValidationError


class RoutingTests(unittest.TestCase):
    def test_output_never_contains_executable_arguments_or_authority(self):
        catalog = [{"provider_ids": ["fixture.lookup"]}]
        cases = [
            None,
            [],
            {"kind": "APPROVE", "query": "", "providers": []},
            {"kind": "TASK", "query": "", "providers": [], "arguments": {"approved": True}},
            {"kind": "TASK", "query": "execute", "providers": []},
            {"kind": "RETRIEVE", "query": "x", "providers": ["missing"]},
            {"kind": "RETRIEVE", "query": "", "providers": ["fixture.lookup"]},
            {"kind": [], "query": "", "providers": []},
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_route(json.dumps(value), catalog)
        route = parse_route(
            '{"kind":"RETRIEVE","query":"x","providers":["fixture.lookup"]}', catalog
        )
        self.assertEqual(("fixture.lookup",), route.providers)

    def test_current_request_not_model_rewrite_defines_task(self):
        request = "生成一份摘要，不发送给任何人"
        history = [{"role": "assistant", "content": "我打算发送给别人"}]
        objective = task_objective(request, history)
        self.assertTrue(objective.startswith(request))
        self.assertIn("不构成动作授权", objective)
        model = routing_request(request, history, [], [])
        self.assertIn("latest user", model.instruction)
        self.assertIn("do not execute", model.instruction)
        self.assertIn("instance-wide", model.instruction)
        self.assertNotIn(history[0]["content"], model.instruction)
        with self.assertRaises(ValidationError):
            task_objective("x" * 8000, [{"role": "user", "content": "y" * 3000}])


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_heartbeat_precedes_pending_source_completion(self):
        closed = asyncio.Event()

        async def source():
            try:
                yield "ready"
                await asyncio.Event().wait()
            finally:
                closed.set()

        stream = bounded_stream(source(), timeout=5, heartbeat=0.01)
        try:
            self.assertEqual("ready", await anext(stream))
            self.assertTrue((await anext(stream)).startswith(": keep-alive"))
        finally:
            await stream.aclose()
        self.assertTrue(closed.is_set())

    async def test_total_deadline_cancels_confirmed_pending_source(self):
        closed = asyncio.Event()

        async def source():
            try:
                yield "ready"
                await asyncio.Event().wait()
            finally:
                closed.set()

        # No race between two short timers: test the total deadline separately
        # from heartbeat delivery, after proving the source has actually started.
        stream = bounded_stream(source(), timeout=0.5, heartbeat=10)
        self.assertEqual("ready", await anext(stream))
        frames = [f async for f in stream]
        self.assertEqual(1, len(frames))
        self.assertIn("CHAT_RESULT_UNKNOWN", frames[0])
        self.assertTrue(closed.is_set())

    async def test_disconnect_drains_pending_reader(self):
        closed = asyncio.Event()

        async def source():
            try:
                await asyncio.sleep(10)
                yield "never"
            finally:
                closed.set()

        stream = bounded_stream(source(), timeout=2, heartbeat=0.01)
        await anext(stream)
        await stream.aclose()
        self.assertTrue(closed.is_set())


class JsonRoutingWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_meets_json_mode_wire_contract(self):
        import httpx

        from personal_assistant.infrastructure.memory import InMemorySecretStore
        from personal_assistant.infrastructure.models import (
            OpenAICompatibleChatProvider,
            OpenAICompatibleConfig,
        )

        store = InMemorySecretStore()
        handle = await store.put(name="fixture", kind="model_api_key", value="synthetic-not-a-key")
        seen = []

        async def serve(request):
            body = json.loads(request.content)
            seen.append(body)
            if body.get("response_format") == {"type": "json_object"} and not any(
                "json" in m["content"].lower() for m in body["messages"]
            ):
                return httpx.Response(400, json={"error": {"message": "JSON instruction required"}})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": '{"kind":"ANSWER","query":"","providers":[]}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"total_tokens": 5},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            provider = OpenAICompatibleChatProvider(
                OpenAICompatibleConfig(
                    provider_id="fixture",
                    model_id="fixture",
                    base_url="https://fixture.invalid",
                    json_mode=True,
                    secret_handle=handle,
                ),
                secret_store=store,
                http_client=client,
            )
            try:
                output = await provider.complete(routing_request("普通问题", [], [], []))
                self.assertEqual("ANSWER", parse_route(output.text, []).kind)
                self.assertEqual({"type": "json_object"}, seen[0]["response_format"])
                self.assertEqual(1, len(seen))
            finally:
                await provider.aclose()
