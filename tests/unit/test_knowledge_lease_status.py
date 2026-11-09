"""Lease status comes from the database clock, not the observing Worker."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from personal_assistant_sdk import RuntimeContext
from personal_knowledge.indexing import BUILD_LEASE_KEY
from personal_knowledge.worker import PersonalKnowledgeExtension

from tests.unit.fake_knowledge_store import FakeKnowledgeStore


def make_worker(tmp_path, monkeypatch):
    (tmp_path / "audit.md").write_text("synthetic indexing keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    monkeypatch.setattr("personal_knowledge.worker.KnowledgeStore", lambda _: store)
    worker = PersonalKnowledgeExtension()
    worker._runtime = RuntimeContext(
        "1", "personal.knowledge", "test", "ext_test",
        non_secret_config={"roots": [{"key": "notes", "path": str(tmp_path)}]},
        host_data=object(),
    )
    return worker, store


async def test_expired_foreign_build_does_not_hide_completed_failure(tmp_path, monkeypatch):
    worker, store = make_worker(tmp_path, monkeypatch)
    monkeypatch.setattr("personal_knowledge.indexing.WAIT_SECONDS", 0)
    index = worker._require_index()
    assert await store.claim_build("other", index._binding_hash, 60)
    worker._indexing_task = asyncio.create_task(worker._automatic_index())
    await worker._indexing_task
    assert worker._indexing_error == "INDEX_BUILD_BUSY"
    store.meta[BUILD_LEASE_KEY]["expires_at"] = 0
    assert await worker._workspace_index_status() == {
        "index_state": "FAILED", "index_reason": "INDEX_BUILD_BUSY",
    }


async def test_abandoned_foreign_build_reports_expired_not_indexing(tmp_path, monkeypatch):
    worker, store = make_worker(tmp_path, monkeypatch)
    index = worker._require_index()
    assert await store.claim_build("other", index._binding_hash, 60)
    store.meta[BUILD_LEASE_KEY]["expires_at"] = 0
    assert await worker._workspace_index_status() == {
        "index_state": "PENDING", "index_reason": "INDEX_LEASE_EXPIRED",
    }


@pytest.mark.parametrize("database_active", [False, True])
async def test_database_authoritative_liveness_wins_over_wall_clock(
    tmp_path, monkeypatch, database_active,
):
    worker, store = make_worker(tmp_path, monkeypatch)
    index = worker._require_index()
    assert await store.claim_build("other", index._binding_hash, 60)
    # Deliberately contradict the raw expiry. The Worker must use DB evaluation.
    store.meta[BUILD_LEASE_KEY]["expires_at"] = 0 if database_active else 10**20
    store.get_build_lease = AsyncMock(return_value={
        **store.meta[BUILD_LEASE_KEY], "active": database_active,
    })
    status = await worker._workspace_index_status()
    assert (status["index_state"] == "INDEXING") is database_active
    store.get_build_lease.assert_awaited_once_with()
