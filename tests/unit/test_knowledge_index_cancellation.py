"""Index cancellation joins I/O and completes owner-CAS cleanup before returning."""

import asyncio
import threading

import pytest
from personal_knowledge.indexing import BUILD_LEASE_KEY, build_index

from tests.unit.fake_knowledge_store import FakeKnowledgeStore
from tests.unit.test_knowledge_binding import make_index


async def test_repeated_cancel_joins_scan_io_before_releasing_build_owner(tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text("synthetic index keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    original_read = index._scan_file
    original_finish = store.finish_build
    finishes = []

    def held_read(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(5), "test cleanup did not release scan"
            return original_read(*args, **kwargs)
        finally:
            completed.set()

    async def finish(owner, state):
        assert completed.is_set(), "the owner must cover the complete in-flight I/O"
        finishes.append((owner, state))
        return await original_finish(owner, state)

    monkeypatch.setattr(index, "_scan_file", held_read)
    monkeypatch.setattr(store, "finish_build", finish)
    task = asyncio.create_task(build_index(index, automatic=True))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        assert not finishes
        assert not await store.claim_build("premature", "other-binding", 60)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert completed.is_set()
        assert len(finishes) == 1 and finishes[0][1] == "CANCELLED"
        assert store.meta[BUILD_LEASE_KEY]["state"] == "CANCELLED"
        assert await store.claim_build("successor", "other-binding", 60)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("finish_fails", [False, True])
async def test_first_and_repeated_cancel_during_finish_waits_and_preserves_error(
    tmp_path, monkeypatch, finish_fails
):
    (tmp_path / "note.md").write_text("synthetic index keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    entered, release = asyncio.Event(), asyncio.Event()
    original_finish = store.finish_build
    failure = RuntimeError("synthetic finish failure")
    finished = False

    async def held_finish(owner, state):
        nonlocal finished
        entered.set()
        await release.wait()
        finished = True
        if finish_fails:
            raise failure
        return await original_finish(owner, state)

    monkeypatch.setattr(store, "finish_build", held_finish)
    task = asyncio.create_task(build_index(index, automatic=True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done(), "cancel must wait for the owner-CAS result"
        assert not finished
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 5)
        assert finished
        if finish_fails:
            assert caught.value.__cause__ is failure
            assert store.meta[BUILD_LEASE_KEY]["state"] == "RUNNING"
        else:
            assert store.meta[BUILD_LEASE_KEY]["state"] == "DONE"
            assert await store.claim_build("successor", "other-binding", 60)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
