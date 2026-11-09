import asyncio
from unittest.mock import AsyncMock

import pytest
from personal_assistant_sdk import RuntimeContext
from personal_knowledge.indexing import IndexBuildError, build_index
from personal_knowledge.worker import PersonalKnowledgeExtension

from tests.unit.fake_knowledge_store import FakeKnowledgeStore
from tests.unit.test_knowledge_binding import make_index


async def test_two_replicas_automatically_build_only_once(tmp_path):
    (tmp_path / "a.md").write_text("automatic keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    first, second = make_index(tmp_path, store), make_index(tmp_path, store)
    entered, release = asyncio.Event(), asyncio.Event()
    original = first.scan

    async def scan():
        entered.set()
        await release.wait()
        return await original()

    first.scan = scan
    second.scan = AsyncMock(wraps=second.scan)
    a = asyncio.create_task(build_index(first, automatic=True, poll_seconds=0.01))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        b = asyncio.create_task(build_index(second, automatic=True, poll_seconds=0.01))
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(a, b), 5)
        assert second.scan.await_count == 0
        assert (await second.search("keyword")).results
        await build_index(second, automatic=True)
        assert second.scan.await_count == 0
    finally:
        release.set()
        for task in (a, locals().get("b")):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_failed_automatic_build_is_not_retried_until_manual_request(tmp_path):
    (tmp_path / "a.md").write_text("automatic keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    first = make_index(tmp_path, store)
    first.scan = AsyncMock(side_effect=RuntimeError("synthetic failure"))
    with pytest.raises(IndexBuildError, match="INDEX_BUILD_FAILED"):
        await build_index(first, automatic=True)
    assert first.scan.await_count == 1
    second = make_index(tmp_path, store)
    second.scan = AsyncMock(wraps=second.scan)
    with pytest.raises(IndexBuildError, match="INDEX_BUILD_FAILED"):
        await build_index(second, automatic=True)
    assert second.scan.await_count == 0
    await build_index(second, automatic=False)
    assert (await second.index_status())["index_state"] == "READY"


async def test_shutdown_cancels_build_and_releases_lease(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("automatic keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    monkeypatch.setattr("personal_knowledge.worker.KnowledgeStore", lambda _: store)
    worker = PersonalKnowledgeExtension()
    entered = asyncio.Event()

    async def scan(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("personal_knowledge.index.KnowledgeIndex.scan", scan)
    await worker.initialize(
        RuntimeContext(
            "1",
            "personal.knowledge",
            "test",
            "ext_test",
            non_secret_config={"roots": [{"key": "notes", "path": str(tmp_path)}]},
            host_data=object(),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
    finally:
        await worker.shutdown()
    assert worker._indexing_task is None
    assert await store.claim_build("new-owner", "new-binding", 60)


async def test_expired_lease_cannot_publish_ready_or_release_successor(tmp_path):
    (tmp_path / "a.md").write_text("automatic keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    entered, release = asyncio.Event(), asyncio.Event()
    original = index.scan

    async def scan():
        entered.set()
        await release.wait()
        return await original()

    index.scan = scan
    task = asyncio.create_task(build_index(index, automatic=True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        store.meta["index.build.lease.v1"]["expires_at"] = 0
        assert await store.claim_build("successor", "next-binding", 60)
        release.set()
        with pytest.raises(IndexBuildError, match="INDEX_BUILD_FAILED"):
            await asyncio.wait_for(task, 5)
        assert (await index.index_status())["index_state"] != "READY"
        assert store.meta["index.build.lease.v1"]["owner"] == "successor"
        assert store.meta["index.build.lease.v1"]["state"] == "RUNNING"
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_embedding_settings_change_require_automatic_rebuild(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("automatic keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    monkeypatch.setattr("personal_knowledge.worker.KnowledgeStore", lambda _: store)
    worker = PersonalKnowledgeExtension()
    worker._runtime = RuntimeContext(
        "1",
        "personal.knowledge",
        "test",
        "ext_test",
        non_secret_config={
            "roots": [{"key": "notes", "path": str(tmp_path)}],
            "embedding": {"provider": "none", "model": "before"},
        },
        host_data=object(),
    )
    await build_index(worker._require_index(), automatic=True)
    changed = PersonalKnowledgeExtension()
    from dataclasses import replace

    changed._runtime = replace(
        worker._runtime,
        non_secret_config={
            **worker._runtime.non_secret_config,
            "embedding": {"provider": "none", "model": "after"},
        },
    )
    assert (await changed._workspace_index_status())["index_state"] == "PENDING"
    await build_index(changed._require_index(), automatic=True)
    assert (await changed._workspace_index_status())["index_state"] == "READY"


async def test_workspace_reports_background_state_and_durable_success_wins(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("automatic keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    monkeypatch.setattr("personal_knowledge.worker.KnowledgeStore", lambda _: store)
    worker = PersonalKnowledgeExtension()
    worker._runtime = RuntimeContext(
        "1",
        "personal.knowledge",
        "test",
        "ext_test",
        non_secret_config={"roots": [{"key": "notes", "path": str(tmp_path)}]},
        host_data=object(),
    )
    index = worker._require_index()
    assert await store.claim_build("other", index._binding_hash, 60)
    assert (await worker._workspace_index_status())["index_state"] == "INDEXING"
    assert await store.finish_build("other", "FAILED")
    assert (await worker._workspace_index_status()) == {
        "index_state": "FAILED",
        "index_reason": "INDEX_BUILD_FAILED",
    }
    worker._indexing_error = "INDEX_BUILD_FAILED"
    await build_index(index, automatic=False)
    assert (await worker._workspace_index_status())["index_state"] == "READY"
