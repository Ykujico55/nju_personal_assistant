"""Conversation diagnosis through the actual API and synthetic model transport."""
from __future__ import annotations

import asyncio
import json

import pytest

from personal_assistant.api import workbench_conversation as api
from personal_assistant.core.models.provider import ModelStreamEvent
from tests.integration.test_conversation_model_failures import scenario

DIAGNOSTICS = "workbench-conversation-diagnostics"
FIRST = "req_" + "1" * 32
SECOND = "req_" + "2" * 32


async def request(env, request_id=FIRST):
    response = await env.service._client.post(
        "/admin/v1/workbench/conversation/stream",
        json={"message": "public synthetic question", "history": []},
        headers={"Idempotency-Key": "diagnostic-fixture", "X-Request-ID": request_id},
    )
    frames = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ")]
    return response, frames[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("case,reason", [
    ("timeout", "MODEL_TIMEOUT"),
    ("network", "MODEL_CONNECTION_FAILED"),
    ("eof", "MODEL_STREAM_INVALID"),
])
async def test_original_failure_diagnosis_is_replayed_without_redispatch(tmp_path, case, reason):
    async with scenario(tmp_path, case) as env:
        first_response, first = await request(env)
        assert first["code"] == "CHAT_RESULT_UNKNOWN" and first["uncertain"] is True
        diagnosis = first["diagnostic"]
        assert diagnosis["reason_code"] == reason and diagnosis["stage"] == "ANSWER"
        assert diagnosis["request_id"] == FIRST and diagnosis["persisted"] is True
        assert type(diagnosis["elapsed_ms"]) is int and diagnosis["elapsed_ms"] >= 0
        record = (await env.container.records.list(api.BUCKET))[0]
        assert record["version"] == 2 and record["value"] == {"state": "STREAMING"}
        second_response, second = await request(env, SECOND)
        assert second["diagnostic"] == diagnosis
        assert first_response.headers["X-Request-ID"] == FIRST
        assert second_response.headers["X-Request-ID"] == SECOND
        assert len(env.calls) == 1 and env.provider.route_calls == 1 and env.wire.closed
        assert not await env.container.tasks.list_recent(limit=20)
        events = await env.container.audit_writer.list_events()
        failures = [e for e in events if e.event_type == "conversation.failed"]
        assert len(failures) == 1 and failures[0].data["reason_code"] == reason
        assert "private" not in json.dumps([first, second, failures[0].data])


@pytest.mark.asyncio
async def test_failed_final_receipt_is_distinct_from_model_failure(tmp_path):
    async with scenario(tmp_path, "success") as env:
        original = env.container.records.put

        async def fail_success(bucket, key, value, **kwargs):
            if bucket == api.BUCKET and value.get("state") == "SUCCEEDED":
                raise RuntimeError("private database content")
            return await original(bucket, key, value, **kwargs)

        env.container.records.put = fail_success
        _, first = await request(env)
        assert first["diagnostic"]["reason_code"] == "RECEIPT_WRITE_FAILED"
        assert first["diagnostic"]["stage"] == "RECEIPT"
        _, second = await request(env, SECOND)
        assert second["diagnostic"] == first["diagnostic"] and len(env.calls) == 1
        record = (await env.container.records.list(api.BUCKET))[0]
        assert record["version"] == 2 and record["value"]["state"] == "STREAMING"
        assert "private" not in json.dumps(first)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["write_failed", "commit_reply_lost", "audit_failed"])
async def test_diagnostic_failure_never_loses_unknown_or_invents_persistence(tmp_path, mode):
    async with scenario(tmp_path, "network") as env:
        original = env.container.records.put

        async def failed_put(bucket, key, value, **kwargs):
            if bucket == DIAGNOSTICS:
                if mode == "write_failed":
                    raise RuntimeError("private diagnostic storage")
                if mode == "commit_reply_lost":
                    await original(bucket, key, value, **kwargs)
                    raise RuntimeError("private lost receipt")
            return await original(bucket, key, value, **kwargs)

        env.container.records.put = failed_put
        if mode == "audit_failed":
            original_append = env.container.audit_writer.append

            async def failed_audit(event):
                if event.event_type == "conversation.failed":
                    raise RuntimeError("private audit detail")
                await original_append(event)

            env.container.audit_writer.append = failed_audit
        _, first = await request(env)
        assert first["code"] == "CHAT_RESULT_UNKNOWN" and first["uncertain"] is True
        assert first["diagnostic"]["persisted"] is (mode == "audit_failed")
        _, second = await request(env, SECOND)
        if mode == "write_failed":
            assert "diagnostic" not in second
        else:
            assert second["diagnostic"]["persisted"] is True
            assert second["diagnostic"]["request_id"] == FIRST
        assert len(env.calls) == 1 and env.provider.route_calls == 1
        assert "private" not in json.dumps([first, second])


@pytest.mark.asyncio
async def test_total_deadline_is_not_misclassified_as_reader_cancellation(tmp_path, monkeypatch):
    original = api.bounded_stream
    started, closed = asyncio.Event(), asyncio.Event()

    def short(source, **kwargs):
        return original(source, timeout=0.5, heartbeat=0.01, **kwargs)

    monkeypatch.setattr(api, "bounded_stream", short)
    async with scenario(tmp_path, "success") as env:
        async def waiting_stream(_):
            try:
                started.set()
                yield ModelStreamEvent(delta="public partial")
                await asyncio.Event().wait()
            finally:
                closed.set()

        env.provider.stream = waiting_stream
        _, first = await request(env)
        assert started.is_set() and closed.is_set()
        assert first["diagnostic"]["reason_code"] == "CONVERSATION_TIMEOUT"
        assert first["diagnostic"]["stage"] == "ANSWER"
        _, second = await request(env, SECOND)
        assert second["diagnostic"] == first["diagnostic"]
        assert env.provider.route_calls == 1


@pytest.mark.asyncio
async def test_authoritative_success_wins_over_old_diagnostic(tmp_path):
    async with scenario(tmp_path, "network") as env:
        await request(env)
        record = (await env.container.records.list(api.BUCKET))[0]
        response = {"answer": "confirmed public result", "tools_executed": False}
        await env.container.records.put(api.BUCKET, record["id"],
            {"state": "SUCCEEDED", "response": response}, expected_version=2)
        _, replay = await request(env, SECOND)
        assert replay == response
        assert len(env.calls) == 1 and env.provider.route_calls == 1


@pytest.mark.asyncio
async def test_cancellation_during_confirmed_failure_receipt_keeps_original_state(tmp_path):
    from types import SimpleNamespace

    async with scenario(tmp_path, "length") as env:
        entered = asyncio.Event()
        original = env.container.records.put

        async def wait_before_failed_receipt(bucket, key, value, **kwargs):
            if bucket == api.BUCKET and value.get("state") == "FAILED":
                entered.set()
                await asyncio.Event().wait()
            return await original(bucket, key, value, **kwargs)

        env.container.records.put = wait_before_failed_receipt
        response = await api.conversation_stream(
            api.ConversationRequest(message="synthetic cancellation"),
            SimpleNamespace(state=SimpleNamespace(
                idempotency_key="cancel-fixture", request_id=FIRST)),
            env.container, "local-admin",
        )

        async def collect():
            return [frame async for frame in response.body_iterator]

        reader = asyncio.create_task(collect())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            reader.cancel()
            with pytest.raises(asyncio.CancelledError):
                await reader
        finally:
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            await response.body_iterator.aclose()
        diagnosis = (await env.container.records.list(DIAGNOSTICS))[0]["value"]
        assert diagnosis["reason_code"] == "REQUEST_CANCELLED"
        assert diagnosis["stage"] == "RECEIPT"
        record = (await env.container.records.list(api.BUCKET))[0]
        assert record["version"] == 2 and record["value"]["state"] == "STREAMING"
        assert env.wire.closed and len(env.calls) == 1


@pytest.mark.asyncio
async def test_total_budget_never_cancels_consumer_while_generator_is_yielding():
    closed = asyncio.Event()

    async def source():
        try:
            yield "ready"
            await asyncio.Event().wait()
        finally:
            closed.set()

    async def consumer():
        stream = api.bounded_stream(source(), timeout=0.05, heartbeat=0.01)
        try:
            assert await anext(stream) == "ready"
            # The stream budget must not inject cancellation into unrelated caller work.
            await asyncio.sleep(0.1)
            frames = [frame async for frame in stream]
            assert len(frames) == 1 and "CHAT_RESULT_UNKNOWN" in frames[0]
        finally:
            await stream.aclose()

    task = asyncio.create_task(consumer())
    try:
        await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_status_query_timeout_does_not_become_original_failure(tmp_path, monkeypatch):
    async with scenario(tmp_path, "success") as env:
        _, completed = await request(env)
        assert "answer" in completed
        original_get = env.container.records.get
        original_bound = api.bounded_stream

        async def block_receipt(bucket, key):
            if bucket == api.BUCKET:
                await asyncio.Event().wait()
            return await original_get(bucket, key)

        def short(source, **kwargs):
            return original_bound(source, timeout=0.2, heartbeat=0.01, **kwargs)

        monkeypatch.setattr(api, "bounded_stream", short)
        env.container.records.get = block_receipt
        _, replay = await request(env, SECOND)
        assert replay["code"] == "CHAT_RESULT_UNKNOWN" and replay["uncertain"]
        assert "diagnostic" not in replay
        assert not await env.container.records.list(DIAGNOSTICS)
        rows = await env.container.records.list(api.BUCKET)
        assert rows[0]["value"]["state"] == "SUCCEEDED" and rows[0]["version"] == 3
        assert len(env.calls) == 1 and env.provider.route_calls == 1
