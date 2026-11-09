"""Cancellation resolves an issued claim before finalizing its unique owner."""

import asyncio
import copy
from unittest.mock import AsyncMock

import pytest
from personal_knowledge.indexing import BUILD_LEASE_KEY, build_index

from tests.unit.fake_knowledge_store import FakeKnowledgeStore
from tests.unit.test_knowledge_binding import make_index


@pytest.mark.parametrize("phase", ["before_commit", "after_commit", "rejected"])
async def test_cancel_during_claim_waits_for_result_without_starting_scan(
    tmp_path, monkeypatch, phase,
):
    (tmp_path / "a.md").write_text("synthetic claim cancellation", encoding="utf-8")
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    if phase == "rejected":
        assert await store.claim_build("foreign", "foreign-binding", 60)
    before = copy.deepcopy(store.meta)
    entered, release = asyncio.Event(), asyncio.Event()
    real_claim = store.claim_build
    real_finish = store.finish_build
    finishes = []

    async def claim(*args, **kwargs):
        if phase == "before_commit":
            entered.set()
            await release.wait()
        claimed = await real_claim(*args, **kwargs)
        if phase != "before_commit":
            entered.set()
            await release.wait()
        return claimed

    async def finish(owner, state):
        finishes.append((owner, state))
        return await real_finish(owner, state)

    monkeypatch.setattr(store, "claim_build", claim)
    monkeypatch.setattr(store, "finish_build", finish)
    monkeypatch.setattr(index, "scan", AsyncMock())
    task = asyncio.create_task(build_index(index, automatic=True, poll_seconds=0.01))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        if phase == "before_commit":
            assert not task.done(), "a not-yet-committed claim must be resolved first"
            assert not finishes
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        index.scan.assert_not_awaited()
        assert not store.sources and not store.chunks
        if phase == "rejected":
            assert store.meta == before
            assert not finishes
        else:
            assert store.meta[BUILD_LEASE_KEY]["state"] == "CANCELLED"
            assert len(finishes) == 1
            assert await real_claim("successor", "next-binding", 60)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_committed_owner_is_finished_before_abandoning_stalled_reply(tmp_path, monkeypatch):
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    committed, release = asyncio.Event(), asyncio.Event()
    real_claim = store.claim_build
    real_finish = store.finish_build
    order = []

    async def claim(*args, **kwargs):
        result = await real_claim(*args, **kwargs)
        assert result
        committed.set()
        try:
            await release.wait()
        finally:
            order.append("reply-finished")
        return result

    async def finish(owner, state):
        result = await real_finish(owner, state)
        order.append("owner-finished")
        return result

    monkeypatch.setattr(store, "claim_build", claim)
    monkeypatch.setattr(store, "finish_build", finish)
    task = asyncio.create_task(build_index(index, automatic=True))
    try:
        await asyncio.wait_for(committed.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert order == ["owner-finished", "reply-finished"]
        assert await real_claim("successor", "next-binding", 60)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_late_claim_reply_cleanup_cannot_change_successor(tmp_path, monkeypatch):
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    committed, release = asyncio.Event(), asyncio.Event()
    real_claim = store.claim_build

    async def claim(*args, **kwargs):
        result = await real_claim(*args, **kwargs)
        committed.set()
        await release.wait()
        return result

    monkeypatch.setattr(store, "claim_build", claim)
    task = asyncio.create_task(build_index(index, automatic=True, poll_seconds=0.01))
    try:
        await asyncio.wait_for(committed.wait(), 5)
        store.meta[BUILD_LEASE_KEY]["expires_at"] = 0
        assert await real_claim("successor", "new-binding", 60)
        assert await store.finish_build("successor", "DONE")
        before = copy.deepcopy(store.meta)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert store.meta == before
        assert not store.sources and not store.chunks
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_claim_finishing_does_not_cancel_pending_owner_query(tmp_path, monkeypatch):
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    committed, reply = asyncio.Event(), asyncio.Event()
    queried, query_reply = asyncio.Event(), asyncio.Event()
    real_claim, real_get = store.claim_build, store.get_meta
    query_cancelled = False

    async def claim(*args, **kwargs):
        result = await real_claim(*args, **kwargs)
        committed.set()
        await reply.wait()
        return result

    async def get(key):
        nonlocal query_cancelled
        if key == BUILD_LEASE_KEY:
            queried.set()
            try:
                await query_reply.wait()
            except asyncio.CancelledError:
                query_cancelled = True
                raise
        return await real_get(key)

    monkeypatch.setattr(store, "claim_build", claim)
    monkeypatch.setattr(store, "get_meta", get)
    task = asyncio.create_task(build_index(index, automatic=True))
    try:
        await asyncio.wait_for(committed.wait(), 5)
        task.cancel()
        await asyncio.wait_for(queried.wait(), 5)
        reply.set()
        await asyncio.sleep(0)
        assert not task.done() and not query_cancelled
        query_reply.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert not query_cancelled
        assert (await store.get_meta(BUILD_LEASE_KEY))["state"] == "CANCELLED"
    finally:
        reply.set()
        query_reply.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_failed_claim_resolution_keeps_cleanup_failure_as_cancel_cause(tmp_path, monkeypatch):
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    entered, release = asyncio.Event(), asyncio.Event()
    failure = RuntimeError("synthetic claim channel failure")
    real_claim, real_get = store.claim_build, store.get_meta

    async def claim(*args, **kwargs):
        await real_claim(*args, **kwargs)
        entered.set()
        await release.wait()
        raise failure

    async def get(key):
        if key == BUILD_LEASE_KEY:
            release.set()
            raise RuntimeError("synthetic query channel failure")
        return await real_get(key)

    monkeypatch.setattr(store, "claim_build", claim)
    monkeypatch.setattr(store, "get_meta", get)
    task = asyncio.create_task(build_index(index, automatic=True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 5)
        assert caught.value.__cause__ is failure
        assert store.meta[BUILD_LEASE_KEY]["state"] == "CANCELLED"
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("phase", ["read_back", "finish"])
async def test_claim_failure_arriving_during_cleanup_is_consumed(tmp_path, monkeypatch, phase):
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    entered, release, failed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    failure = RuntimeError("synthetic late claim response failure")
    real_claim, real_get, real_finish = store.claim_build, store.get_meta, store.finish_build

    async def claim(*args, **kwargs):
        await real_claim(*args, **kwargs)
        entered.set()
        await release.wait()
        failed.set()
        raise failure

    async def let_claim_fail():
        release.set()
        await failed.wait()

    async def get(key):
        if key == BUILD_LEASE_KEY and phase == "read_back":
            await let_claim_fail()
        return await real_get(key)

    async def finish(owner, state):
        if phase == "finish":
            await let_claim_fail()
        return await real_finish(owner, state)

    monkeypatch.setattr(store, "claim_build", claim)
    monkeypatch.setattr(store, "get_meta", get)
    monkeypatch.setattr(store, "finish_build", finish)
    task = asyncio.create_task(build_index(index, automatic=True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 5)
        assert caught.value.__cause__ is failure
        assert store.meta[BUILD_LEASE_KEY]["state"] == "CANCELLED"
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_abandoned_reply_cleanup_failure_is_not_discarded(tmp_path, monkeypatch):
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    entered, release = asyncio.Event(), asyncio.Event()
    failure = RuntimeError("synthetic response cleanup failure")
    real_claim = store.claim_build

    async def claim(*args, **kwargs):
        await real_claim(*args, **kwargs)
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            raise failure from None
        return True

    monkeypatch.setattr(store, "claim_build", claim)
    task = asyncio.create_task(build_index(index, automatic=True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 5)
        assert caught.value.__cause__ is failure
        assert store.meta[BUILD_LEASE_KEY]["state"] == "CANCELLED"
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
