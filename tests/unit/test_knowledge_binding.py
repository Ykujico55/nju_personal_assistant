from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from personal_assistant_sdk import ContextQuery
from personal_knowledge.embedding import DeterministicTestEmbeddingProvider
from personal_knowledge.index import KnowledgeIndex
from personal_knowledge.models import EvidenceStatus
from personal_knowledge.paths import parse_roots, resolve_roots
from personal_knowledge.worker import DEFAULT_MAX_FILE_BYTES, PersonalKnowledgeExtension

from tests.unit.fake_knowledge_store import FakeKnowledgeStore


def make_index(root, store):
    return KnowledgeIndex(
        store,
        resolve_roots(parse_roots([{"key": "notes", "path": str(root)}])),
        DeterministicTestEmbeddingProvider(dim=16),
        migrations=(),
        max_file_bytes=DEFAULT_MAX_FILE_BYTES,
        extension_id="personal.knowledge",
        extension_version="test",
    )


async def test_same_key_new_root_cannot_reuse_old_index_even_for_identical_bytes(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    for root in (old, new):
        (root / "note.md").write_text("root binding keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    previous = make_index(old, store)
    await previous.reconcile()
    hit = await previous.search("binding")
    assert hit.results
    saved = copy.deepcopy((store.sources, store.chunks, store.meta))

    current = make_index(new, store)
    assert (await current.search("binding")).results == ()
    assert await current.verify(hit.results[0]) is not EvidenceStatus.CURRENT
    assert await current.index_status() == {
        "index_state": "PENDING", "index_reason": "ROOT_BINDING_CHANGED"
    }
    assert (store.sources, store.chunks, store.meta) == saved

    await current.reconcile()
    assert (await current.index_status())["index_state"] == "READY"
    assert (await current.search("binding")).results
    assert (old / "note.md").read_text(encoding="utf-8") == "root binding keyword"


async def test_legacy_unbound_index_is_preserved_until_explicit_reindex(tmp_path):
    (tmp_path / "note.md").write_text("legacy keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    await index.reconcile()
    store.meta.clear()
    saved = copy.deepcopy((store.sources, store.chunks))
    assert (await index.search("legacy")).results == ()
    assert await index.index_status() == {
        "index_state": "PENDING", "index_reason": "INDEX_BINDING_MISSING"
    }
    assert (store.sources, store.chunks) == saved
    await index.reconcile()
    assert (await index.search("legacy")).results


async def test_partial_scan_remains_pending_until_all_files_succeed(tmp_path):
    (tmp_path / "good.md").write_text("good keyword", encoding="utf-8")
    broken = tmp_path / "broken.md"
    broken.write_bytes(b"\xff\x00invalid")
    index = make_index(tmp_path, FakeKnowledgeStore())
    report = await index.reconcile()
    assert report.errors
    assert await index.index_status() == {
        "index_state": "PENDING", "index_reason": "INDEX_SCAN_INCOMPLETE"
    }
    assert (await index.search("good")).results == ()
    broken.write_text("repaired keyword", encoding="utf-8")
    await index.reconcile()
    assert (await index.index_status())["index_state"] == "READY"
    assert (await index.search("good")).results


async def test_complete_empty_scan_is_ready(tmp_path):
    index = make_index(tmp_path, FakeKnowledgeStore())
    await index.reconcile()
    assert (await index.index_status())["index_state"] == "READY"
    assert (await index.search("missing")).results == ()


async def test_late_scan_cannot_mark_newer_incomplete_scan_ready(tmp_path):
    (tmp_path / "note.md").write_text("parallel keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    older, newer = make_index(tmp_path, store), make_index(tmp_path, store)
    entered, release = asyncio.Event(), asyncio.Event()
    scan = older.scan

    async def delayed_scan():
        entered.set()
        await release.wait()
        return await scan()

    async def failed_scan():
        raise RuntimeError("synthetic scan failure")

    older.scan = delayed_scan
    newer.scan = failed_scan
    task = asyncio.create_task(older.reconcile())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(RuntimeError, match="synthetic scan failure"):
            await newer.reconcile()
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert (await newer.index_status())["index_state"] == "PENDING"


async def test_workspace_reports_binding_status_without_index_or_body_reads(tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text("private body", encoding="utf-8")
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    await index.reconcile()
    worker = PersonalKnowledgeExtension()
    worker._runtime = SimpleNamespace(
        non_secret_config={"roots": [{"key": "notes", "path": str(tmp_path)}]},
        extension_id="personal.knowledge", extension_version="test", host_data=object(),
    )
    worker._require_index = Mock(side_effect=AssertionError("overview must not build an index"))
    monkeypatch.setattr("personal_knowledge.worker.KnowledgeStore", lambda _: store)
    monkeypatch.setattr(type(tmp_path), "read_bytes", Mock(side_effect=AssertionError("body read")))
    result = await worker.retrieve(ContextQuery(
        "workspace", filters={"provider_ids": ["knowledge.workspace"]}
    ))
    data = json.loads(result[0].text)
    assert data["index_state"] == "READY"
    assert data["index_reason"] == "INDEX_READY"
    assert data["files"][0]["index_state"] == "UNKNOWN"
    assert "private body" not in result[0].text
    assert str(tmp_path) not in result[0].text
    assert not worker._require_index.called


@pytest.mark.parametrize("option", ["include_hidden", "max_file_bytes"])
async def test_changed_scan_options_require_new_complete_scan(tmp_path, option):
    (tmp_path / "note.md").write_text("scoped keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    original = make_index(tmp_path, store)
    await original.reconcile()
    changed = KnowledgeIndex(
        store, original.roots, original.embedder, migrations=(),
        max_file_bytes=1024 if option == "max_file_bytes" else DEFAULT_MAX_FILE_BYTES,
        include_hidden=option == "include_hidden",
        extension_id="personal.knowledge", extension_version="test",
    )
    saved = copy.deepcopy((store.sources, store.chunks))
    assert (await changed.index_status())["index_state"] == "PENDING"
    assert (await changed.search("scoped")).results == ()
    assert (store.sources, store.chunks) == saved
    await changed.reconcile()
    assert (await changed.index_status())["index_state"] == "READY"


async def test_failed_activation_keeps_old_chunks_but_does_not_certify_scan(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("original keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    await index.reconcile()
    saved = copy.deepcopy(store.chunks)
    note.write_text("replacement keyword", encoding="utf-8")
    store.fail_activation = True
    assert (await index.reconcile()).errors
    assert store.chunks == saved
    assert (await index.index_status())["index_state"] == "PENDING"
    assert (await index.search("original")).results == ()


async def test_unavailable_binding_metadata_fails_closed(tmp_path):
    index = make_index(tmp_path, FakeKnowledgeStore())

    async def unavailable(_):
        raise RuntimeError("synthetic metadata unavailable")

    index.store.get_meta = unavailable
    assert await index.index_status() == {
        "index_state": "UNAVAILABLE", "index_reason": "INDEX_METADATA_UNAVAILABLE"
    }
    assert (await index.search("missing")).results == ()
