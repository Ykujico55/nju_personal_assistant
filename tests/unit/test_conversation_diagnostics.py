"""Content-free conversation diagnosis: first receipt, cancellation and budgets."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from personal_assistant.api import workbench_conversation_diagnostics as api
from personal_assistant.core.conversation_diagnostics import diagnostic_message, parse_diagnostic
from personal_assistant.core.models.errors import (
    ModelProviderProtocolError,
    ModelProviderResponseTooLargeError,
    ModelProviderTimeoutError,
    ModelProviderUnavailableError,
)
from personal_assistant.infrastructure.memory.audit import InMemoryAuditWriter
from personal_assistant.infrastructure.memory.records import InMemoryRecordStore

if TYPE_CHECKING:
    from personal_assistant.bootstrap import Container

FIRST = "req_" + "a" * 32
SECOND = "req_" + "b" * 32
KEY = "conversation:synthetic-fingerprint"


def value(**changes: Any) -> dict[str, Any]:
    return {
        "reason_code": "MODEL_TIMEOUT", "stage": "ANSWER", "request_id": FIRST,
        "elapsed_ms": 1234, "persisted": True, **changes,
    }


def container() -> SimpleNamespace:
    return SimpleNamespace(records=InMemoryRecordStore(), audit_writer=InMemoryAuditWriter())


def recorder(env: SimpleNamespace, request_id: object = FIRST) -> api.ConversationDiagnostics:
    result = api.ConversationDiagnostics(cast("Container", env), KEY, request_id, "fixture")
    result.stage = "ANSWER"
    return result


@pytest.mark.parametrize("candidate", [
    None, [], {},
    value(reason_code="private provider content"),
    value(stage="private stage"),
    value(request_id="private credential"),
    value(request_id=123),
    value(elapsed_ms=True),
    value(elapsed_ms=-1),
    value(elapsed_ms=86_400_001),
    value(elapsed_ms=float("inf")),
    value(elapsed_ms=1.0),
    value(persisted=1),
    value(private="must not be shown"),
])
def test_untrusted_diagnostic_is_not_displayed(candidate: object) -> None:
    assert parse_diagnostic(candidate) is None
    assert diagnostic_message(candidate) == ""


def test_valid_diagnosis_is_copied_and_unpersisted_status_is_explicit() -> None:
    original = value(request_id=None, persisted=False, elapsed_ms=0)
    parsed = parse_diagnostic(original)
    assert parsed == original and parsed is not original
    assert "未确认保存" in diagnostic_message(original)
    assert "回复生成" in diagnostic_message(original)
    assert parse_diagnostic(value(elapsed_ms=86_400_000)) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,reason", [
    (ModelProviderTimeoutError("private timeout"), "MODEL_TIMEOUT"),
    (ModelProviderUnavailableError("private network"), "MODEL_CONNECTION_FAILED"),
    (ModelProviderResponseTooLargeError("private response"), "MODEL_RESPONSE_TOO_LARGE"),
    (ModelProviderProtocolError("private payload"), "MODEL_STREAM_INVALID"),
    (RuntimeError("private traceback"), "INTERNAL_FAILURE"),
])
async def test_fixed_type_classification_does_not_change_original_receipt(
    failure: Exception, reason: str,
) -> None:
    env = container()
    await env.records.put("workbench-conversation", KEY, {"state": "STREAMING"},
                          expected_version=0)
    original = await env.records.get("workbench-conversation", KEY)
    result = await recorder(env).record(failure)
    assert result["reason_code"] == reason and result["persisted"] is True
    assert result["request_id"] == FIRST
    assert await env.records.get("workbench-conversation", KEY) == original
    assert await recorder(env, SECOND).load() == result
    events = await env.audit_writer.list_events()
    assert len(events) == 1 and events[0].data == result
    assert "private" not in json.dumps([result, events[0].data])


@pytest.mark.asyncio
async def test_competing_writer_retains_first_commit_and_does_not_duplicate_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = container()
    original_put = env.records.put
    committed, release = asyncio.Event(), asyncio.Event()

    async def gated_put(bucket: str, key: str, payload: dict[str, Any],
                        *, expected_version: int) -> dict[str, Any]:
        result = await original_put(bucket, key, payload, expected_version=expected_version)
        if payload.get("request_id") == FIRST:
            committed.set()
            await release.wait()
        return cast("dict[str, Any]", result)

    monkeypatch.setattr(env.records, "put", gated_put)
    first = asyncio.create_task(recorder(env).record(reason_code="MODEL_TIMEOUT"))
    try:
        await asyncio.wait_for(committed.wait(), 2)
        second = await recorder(env, SECOND).record(reason_code="MODEL_CONNECTION_FAILED")
        assert second["reason_code"] == "MODEL_TIMEOUT" and second["request_id"] == FIRST
        release.set()
        assert await first == second
        assert await recorder(env, SECOND).record(reason_code="INTERNAL_FAILURE") == second
        stored = await env.records.get(api.BUCKET, KEY)
        assert stored["version"] == 1 and stored["value"] == second
        assert len(await env.audit_writer.list_events()) == 1
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_committed_diagnostic_with_lost_reply_is_recovered_on_later_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = container()
    original_put = env.records.put

    async def lost_reply(bucket: str, key: str, payload: dict[str, Any],
                         *, expected_version: int) -> dict[str, Any]:
        await original_put(bucket, key, payload, expected_version=expected_version)
        raise RuntimeError("private transport detail")

    monkeypatch.setattr(env.records, "put", lost_reply)
    first = await recorder(env).record(reason_code="MODEL_TIMEOUT")
    assert first["persisted"] is False
    second = await recorder(env, SECOND).record(reason_code="INTERNAL_FAILURE")
    assert second == {**first, "persisted": True}
    assert await recorder(env).load() == second
    assert (await env.records.get(api.BUCKET, KEY))["version"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", [
    value(reason_code="private corrupt value"), value(persisted=False), value(extra="private"),
])
async def test_invalid_existing_diagnostic_is_not_displayed_or_overwritten(
    prior: dict[str, Any],
) -> None:
    env = container()
    await env.records.put(api.BUCKET, KEY, prior, expected_version=0)
    before = await env.records.get(api.BUCKET, KEY)
    diagnostic = recorder(env, SECOND)
    assert await diagnostic.load() is None
    result = await diagnostic.record(reason_code="MODEL_TIMEOUT")
    assert result["persisted"] is False
    assert result["request_id"] == SECOND
    assert await env.records.get(api.BUCKET, KEY) == before
    assert await env.audit_writer.list_events() == []
    assert "private" not in json.dumps(result)


@pytest.mark.asyncio
async def test_external_cancellation_propagates_and_does_not_invent_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = container()
    entered, closed = asyncio.Event(), asyncio.Event()

    async def blocked_put(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        finally:
            closed.set()

    monkeypatch.setattr(env.records, "put", blocked_put)
    pending = asyncio.create_task(recorder(env).record(reason_code="REQUEST_CANCELLED"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert closed.is_set()
        assert await env.records.get(api.BUCKET, KEY) is None
        assert await env.audit_writer.list_events() == []
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["write", "audit", "load"])
async def test_diagnostic_budget_bounds_confirmed_pending_io_and_drains_cancellation(
    monkeypatch: pytest.MonkeyPatch, blocked: str,
) -> None:
    env = container()
    entered, closed = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(api, "_DIAGNOSTIC_BUDGET", 0.2)

    async def blocked_io(*args: Any, **kwargs: Any) -> Any:
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    if blocked == "write":
        monkeypatch.setattr(env.records, "put", blocked_io)
    elif blocked == "audit":
        monkeypatch.setattr(env.audit_writer, "append", blocked_io)
    else:
        monkeypatch.setattr(env.records, "get", blocked_io)
    diagnostic = recorder(env)
    pending = asyncio.create_task(
        diagnostic.load() if blocked == "load" else diagnostic.record(reason_code="MODEL_TIMEOUT")
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        result = await asyncio.wait_for(pending, 2)
        assert closed.is_set()
        if blocked == "load":
            assert result is None
        else:
            assert result is not None
            assert result["persisted"] is (blocked == "audit")
        records = await env.records.list(api.BUCKET)
        assert len(records) == (1 if blocked == "audit" else 0)
        assert await env.audit_writer.list_events() == []
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
