import asyncio
import copy
import threading

import pytest
from personal_knowledge.indexing import IndexBuildError, build_index

from tests.unit.fake_knowledge_store import FakeKnowledgeStore
from tests.unit.test_knowledge_binding import make_index


async def test_expired_scanner_cannot_mutate_successor_ready_index(tmp_path):
    a, b = tmp_path / "old", tmp_path / "new"
    a.mkdir()
    b.mkdir()
    (a / "old.md").write_text("old content", encoding="utf-8")
    (b / "new.md").write_text("successor keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    old, new = make_index(a, store), make_index(b, store)
    entered, release = asyncio.Event(), asyncio.Event()
    original = old.scan

    async def held_scan():
        entered.set()
        await release.wait()
        return await original()

    old.scan = held_scan
    task = asyncio.create_task(build_index(old, automatic=True))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        store.meta["index.build.lease.v1"]["expires_at"] = 0
        await build_index(new, automatic=True)
        assert (await new.index_status())["index_state"] == "READY"
        assert (await new.search("successor")).results
        before = copy.deepcopy((store.sources, store.chunks))
        release.set()
        with pytest.raises(IndexBuildError):
            await asyncio.wait_for(task, 2)
        assert (store.sources, store.chunks) == before
        assert (await new.search("successor")).results
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_background_scan_releases_event_loop_and_cancellation_joins_io(tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text("ordinary text", encoding="utf-8")
    index = make_index(tmp_path, FakeKnowledgeStore())
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    original = index._scan_file

    def blocked_read(*args, **kwargs):
        entered.set()
        try:
            if not release.wait(5):
                raise AssertionError("event loop did not release file I/O")
            return original(*args, **kwargs)
        finally:
            completed.set()

    monkeypatch.setattr(index, "_scan_file", blocked_read)
    scan = asyncio.create_task(index.scan())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert not completed.is_set()
        scan.cancel()
        await asyncio.sleep(0)
        assert not scan.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(scan, 5)
        assert completed.is_set()
    finally:
        release.set()
        await asyncio.gather(scan, return_exceptions=True)
