from __future__ import annotations

import asyncio
import json
import unittest

from personal_assistant.api.workbench_conversation import bounded_stream
from personal_assistant.core import conversation
from personal_assistant.core.conversation import parse_route, routing_request, task_objective
from personal_assistant.domain import ValidationError


class RoutingTests(unittest.TestCase):
    def test_overview_without_search_words_uses_current_request_as_read_query(self):
        catalog = [{"provider_ids": ["fixture.overview"]}]
        route = parse_route(
            '{"kind":"RETRIEVE","query":"  ","providers":["fixture.overview"]}',
            catalog, fallback_query="  当前知识库文件有哪些  ",
        )
        self.assertEqual("RETRIEVE", route.kind)
        self.assertEqual("当前知识库文件有哪些", route.query)
        self.assertEqual(("fixture.overview",), route.providers)

    def test_fallback_does_not_replace_an_explicit_document_target(self):
        route = parse_route(
            json.dumps({"kind": "RETRIEVE", "query": "Exact filename.md",
                        "providers": ["fixture.document"]}),
            [{"provider_ids": ["fixture.document"]}],
            fallback_query="请读取这份文件，不执行其他动作",
        )
        self.assertEqual("Exact filename.md", route.query)

    def test_fallback_does_not_authorize_unknown_providers_or_additional_fields(self):
        catalog = [{"provider_ids": ["fixture.overview"]}]
        cases = [
            {"kind": "RETRIEVE", "query": "", "providers": ["unregistered"]},
            {"kind": "RETRIEVE", "query": "", "providers": []},
            {"kind": "RETRIEVE", "query": "", "providers": ["fixture.overview"],
             "approved": True},
            {"kind": "TASK", "query": "", "providers": ["fixture.overview"]},
            {"kind": "ANSWER", "query": "unexpected", "providers": []},
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_route(json.dumps(value), catalog, fallback_query="列举文件")

    def test_fallback_must_be_nonempty_and_is_never_truncated(self):
        text = '{"kind":"RETRIEVE","query":"","providers":["fixture.overview"]}'
        catalog = [{"provider_ids": ["fixture.overview"]}]
        for fallback in (None, "", "  ", "字" * 2001):
            with self.subTest(length=len(fallback or "")), self.assertRaises(ValidationError):
                parse_route(text, catalog, fallback_query=fallback)
        self.assertEqual("字" * 2000, parse_route(
            text, catalog, fallback_query="字" * 2000,
        ).query)

    def test_route_validation_has_fixed_content_free_reasons(self):
        catalog = [{"provider_ids": ["fixture.overview"]}]
        cases = [
            ("private malformed model text", "ROUTE_FORMAT_INVALID"),
            (json.dumps({"kind": "RETRIEVE", "query": "", "providers": ["private-id"]}),
             "ROUTE_PROVIDER_UNAVAILABLE"),
            (json.dumps({"kind": "RETRIEVE", "query": "", "providers": ["fixture.overview"]}),
             "ROUTE_QUERY_REQUIRED"),
            (json.dumps({"kind": "ANSWER", "query": "private-content", "providers": []}),
             "ROUTE_CONTEXT_UNEXPECTED"),
        ]
        for text, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(ValidationError) as raised:
                parse_route(text, catalog)
            self.assertEqual(
                expected, conversation.conversation_validation_reason(raised.exception),
            )
            self.assertNotIn("private", str(raised.exception))
        with self.assertRaises(ValidationError) as raised:
            task_objective("x" * 10001, [])
        self.assertEqual("TASK_CONTEXT_TOO_LARGE",
                         conversation.conversation_validation_reason(raised.exception))

        class PrivateError(ValidationError):
            def __str__(self) -> str:
                raise AssertionError("exception text must not be read")

        self.assertEqual("CONVERSATION_VALIDATION_FAILED",
                         conversation.conversation_validation_reason(PrivateError()))

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
