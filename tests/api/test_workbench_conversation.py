from __future__ import annotations

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from personal_assistant.core.models import ContextField, DataClassification, ModelOutput
from tests.api import test_workbench_stream as fixture

events, PREFIX = fixture.events, fixture.PREFIX


class ConversationTests(unittest.TestCase):
    setUp = fixture.WorkbenchStreamTests.setUp
    provider = fixture.WorkbenchStreamTests.provider

    def post(self, body=None, key="one"):
        return self.client.post(
            PREFIX + "/conversation/stream",
            json=body or {"message": "解释本地资料里的数据结构"},
            headers={"Idempotency-Key": key},
        )

    def plan(self, kind="ANSWER", **extra):
        plan = {"kind": kind, "query": "", "providers": [], **extra}
        model = AsyncMock(
            return_value=ModelOutput(
                text=json.dumps(plan),
                provider_id="remote.openai",
                model_id="fixture",
                usage={"total_tokens": 3},
            )
        )
        self.container.model_router.complete = model
        self.provider()
        return model

    def test_plain_answer_routes_then_streams_without_task(self):
        model = self.plan()
        response = self.post()
        self.assertEqual(200, response.status_code, response.text)
        received = events(response)
        self.assertEqual(["delta", "completed"], [e[0] for e in received])
        self.assertEqual(10, received[-1][1]["usage"]["total_tokens"])
        self.assertEqual([], self.client.get(PREFIX + "/tasks").json()["items"])
        self.post()
        self.assertEqual(1, model.await_count)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(409, self.post({"message": "changed"}).status_code)

    def test_task_request_creates_one_real_queue_job(self):
        model = self.plan("TASK")
        response = self.post({"message": "请建立索引并生成一份整理结果"})
        self.assertEqual(200, response.status_code, response.text)
        result = events(response)[-1][1]
        self.assertEqual("QUEUED", result["task"]["state"])
        self.assertIn("尚未完成", result["answer"])
        self.assertEqual([], self.calls)
        repeat = events(self.post({"message": "请建立索引并生成一份整理结果"}))[-1][1]
        self.assertEqual(result["task"], repeat["task"])
        self.assertEqual(1, model.await_count)
        self.assertEqual(1, len(self.client.get(PREFIX + "/tasks").json()["items"]))
        queued = self.client.get(PREFIX + "/task-queue").json()["items"]
        self.assertTrue(queued[0]["queue_job_id"])

    def test_retrieval_uses_instance_provider_and_cites_source(self):
        self.plan("RETRIEVE", query="数据结构", providers=["fixture.retrieve"])
        retrieved = SimpleNamespace(
            fields=(
                ContextField(
                    "evidence", "资料摘录", DataClassification.PERSONAL, "file:///fixture.txt"
                ),
            ),
            citations=({"source_uri": "file:///fixture.txt", "locator": {"line_start": 1}},),
            warnings=(),
        )
        with patch(
            "personal_assistant.api.workbench_conversation.ExtensionContextService"
        ) as service:
            service.return_value.catalog = AsyncMock(
                return_value=({"provider_ids": ["fixture.retrieve"]},)
            )
            service.return_value.retrieve = AsyncMock(return_value=retrieved)
            result = events(self.post())[-1][1]
            self.assertEqual("file:///fixture.txt", result["sources"][0]["source_uri"])
            self.assertEqual("数据结构", service.return_value.retrieve.call_args.args[0])
        self.assertIn("资料摘录", str(self.calls[0][0].fields))
        self.assertEqual([], self.client.get(PREFIX + "/tasks").json()["items"])

    def test_invalid_or_forged_route_cannot_create_task(self):
        for i, plan in enumerate(
            (
                {"kind": "TASK", "query": "", "providers": [], "approved": True},
                {"kind": "RETRIEVE", "query": "x", "providers": ["unknown"]},
            )
        ):
            self.plan()
            self.container.model_router.complete.return_value = ModelOutput(
                text=json.dumps(plan), provider_id="remote.openai", model_id="fixture"
            )
            result = events(self.post(key=str(i)))[-1]
            self.assertEqual("error", result[0])
            self.assertFalse(result[1]["uncertain"])
        self.assertEqual([], self.client.get(PREFIX + "/tasks").json()["items"])

    def test_uncertain_intent_never_dispatches_again(self):
        model = self.plan()
        model.side_effect = RuntimeError("secret diagnostic")
        first = events(self.post())[-1]
        self.assertTrue(first[1]["uncertain"])
        self.assertNotIn("secret diagnostic", str(first))
        self.post()
        self.assertEqual(1, model.await_count)

    def test_manual_mode_and_cross_origin_cannot_dispatch(self):
        model = self.plan()
        self.container.settings = replace(self.settings, model_disclosure_mode="manual")
        self.assertEqual(409, self.post().status_code)
        self.assertEqual(0, model.await_count)
        r = self.client.post(
            PREFIX + "/conversation/stream",
            json={"message": "x"},
            headers={"Idempotency-Key": "evil", "Origin": "https://evil.test"},
        )
        self.assertEqual(403, r.status_code)

    def test_partial_answer_failure_keeps_original_key_and_no_task(self):
        model = self.plan()
        self.provider(failure=True)
        first = events(self.post())[-1]
        self.assertTrue(first[1]["uncertain"])
        self.post()
        self.assertEqual(1, model.await_count)
        self.assertEqual(1, len(self.calls))
        self.assertEqual([], self.client.get(PREFIX + "/tasks").json()["items"])

    def test_context_failure_is_visible_and_never_turns_into_action(self):
        self.plan("RETRIEVE", query="资料", providers=["fixture.retrieve"])
        retrieved = SimpleNamespace(
            fields=(), citations=(), warnings=("CONTEXT_PROVIDER_UNAVAILABLE",)
        )
        with patch(
            "personal_assistant.api.workbench_conversation.ExtensionContextService"
        ) as service:
            service.return_value.catalog = AsyncMock(
                return_value=({"provider_ids": ["fixture.retrieve"]},)
            )
            service.return_value.retrieve = AsyncMock(return_value=retrieved)
            result = events(self.post())[-1][1]
        self.assertIn("CONTEXT_PROVIDER_UNAVAILABLE", result["warnings"])
        self.assertEqual([], result["sources"])
        self.assertEqual([], self.client.get(PREFIX + "/tasks").json()["items"])

    def test_task_receipt_loss_recovers_same_creation_without_model(self):
        self.plan("TASK")
        original = self.container.records.put
        failed = False

        async def put(bucket, key, value, **kwargs):
            nonlocal failed
            if (
                bucket == "workbench-conversation"
                and value.get("state") == "SUCCEEDED"
                and not failed
            ):
                failed = True
                raise RuntimeError("lost receipt")
            return await original(bucket, key, value, **kwargs)

        with patch.object(self.container.records, "put", side_effect=put):
            self.assertEqual("error", events(self.post())[-1][0])
        recovered = events(self.post())[-1]
        self.assertEqual("completed", recovered[0])
        self.assertEqual(1, len(self.client.get(PREFIX + "/tasks").json()["items"]))
        self.assertEqual(1, self.container.model_router.complete.await_count)
