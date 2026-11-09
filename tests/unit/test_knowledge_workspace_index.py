"""Overview distinguishes saved index versions from current body verification."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from personal_assistant_sdk import ContextQuery
from personal_knowledge.binding import INDEX_BINDING_KEY
from personal_knowledge.store import KnowledgeStore
from personal_knowledge.worker import PersonalKnowledgeExtension

from tests.unit.fake_knowledge_store import FakeKnowledgeStore
from tests.unit.test_knowledge_binding import make_index


def worker_for(root, store, monkeypatch):
    worker = PersonalKnowledgeExtension()
    worker._runtime = SimpleNamespace(
        non_secret_config={"roots": [{"key": "notes", "path": str(root)}]},
        extension_id="personal.knowledge", extension_version="test", host_data=object(),
    )
    worker._require_index = Mock(side_effect=AssertionError("must not rebuild or read bodies"))
    monkeypatch.setattr("personal_knowledge.worker.KnowledgeStore", lambda _: store)
    return worker


async def overview(worker):
    evidence = await worker.retrieve(ContextQuery(
        "workspace", filters={"provider_ids": ["knowledge.workspace"]},
    ))
    return json.loads(evidence[0].text)


async def test_ready_overview_projects_actual_versions_without_claiming_all_files_indexed(
    tmp_path, monkeypatch,
):
    (tmp_path / "indexed.md").write_text("synthetic indexed body", encoding="utf-8")
    (tmp_path / "changed.md").write_text("old body", encoding="utf-8")
    (tmp_path / "empty.md").write_text("", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"synthetic unsupported image")
    store = FakeKnowledgeStore()
    await make_index(tmp_path, store).reconcile()
    (tmp_path / "changed.md").write_text("changed and longer body", encoding="utf-8")
    (tmp_path / "new.md").write_text("not indexed yet", encoding="utf-8")
    worker = worker_for(tmp_path, store, monkeypatch)
    original = store.workspace_index_records
    store.workspace_index_records = AsyncMock(wraps=original)
    monkeypatch.setattr(type(tmp_path), "read_bytes", Mock(side_effect=AssertionError("no body")))
    data = await overview(worker)
    files = {item["relative_path"]: item for item in data["files"]}
    assert data["index_state"] == "READY"
    assert {name: item["index_state"] for name, item in files.items()} == {
        "indexed.md": "INDEXED", "changed.md": "STALE", "empty.md": "NOT_INDEXED",
        "new.md": "NOT_INDEXED", "image.png": "UNSUPPORTED",
    }
    assert all(item["content_verification"] == "NOT_CHECKED" for item in files.values())
    store.workspace_index_records.assert_awaited_once()
    assert "synthetic indexed body" not in json.dumps(data)
    assert str(tmp_path) not in json.dumps(data)
    assert not worker._require_index.called


async def test_same_metadata_does_not_claim_current_content_hash(tmp_path, monkeypatch):
    path = tmp_path / "same.md"
    path.write_text("old body", encoding="utf-8")
    store = FakeKnowledgeStore()
    await make_index(tmp_path, store).reconcile()
    stat = path.stat()
    path.write_text("new body", encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    worker = worker_for(tmp_path, store, monkeypatch)
    monkeypatch.setattr(type(tmp_path), "read_bytes", Mock(side_effect=AssertionError("no body")))
    data = await overview(worker)
    assert data["files"][0]["index_state"] == "INDEXED"
    assert data["files"][0]["content_verification"] == "NOT_CHECKED"
    assert "哈希" in data["note"] and "元数据" in data["note"]


@pytest.mark.parametrize("change", ["hash", "version", "state"])
async def test_invalid_active_version_is_never_presented_as_indexed(tmp_path, monkeypatch, change):
    (tmp_path / "note.md").write_text("synthetic body", encoding="utf-8")
    store = FakeKnowledgeStore()
    await make_index(tmp_path, store).reconcile()
    source = next(iter(store.sources.values()))
    version = store.versions[(source["source_id"], source["active_version"])]
    if change == "hash":
        version["content_hash"] = "a" * 64
    elif change == "version":
        del store.versions[(source["source_id"], source["active_version"])]
    else:
        version["state"] = "FAILED"
    data = await overview(worker_for(tmp_path, store, monkeypatch))
    assert data["files"][0]["index_state"] == "STALE"
    assert data["files"][0]["content_verification"] == "NOT_CHECKED"


async def test_binding_change_during_metadata_read_keeps_file_state_unknown(tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text("synthetic body", encoding="utf-8")
    store = FakeKnowledgeStore()
    await make_index(tmp_path, store).reconcile()
    original = store.workspace_index_records

    async def changed(files):
        result = await original(files)
        store.meta[INDEX_BINDING_KEY] = dict(store.meta[INDEX_BINDING_KEY], binding_hash="changed")
        return result

    store.workspace_index_records = changed
    data = await overview(worker_for(tmp_path, store, monkeypatch))
    assert data["index_state"] == "PENDING"
    assert data["index_reason"] == "ROOT_BINDING_CHANGED"
    assert data["files"][0]["index_state"] == "UNKNOWN"


async def test_unavailable_file_metadata_is_unknown_not_not_indexed(tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text("synthetic body", encoding="utf-8")
    store = FakeKnowledgeStore()
    await make_index(tmp_path, store).reconcile()
    store.workspace_index_records = AsyncMock(side_effect=RuntimeError("synthetic private failure"))
    data = await overview(worker_for(tmp_path, store, monkeypatch))
    assert data["index_state"] == "UNAVAILABLE"
    assert data["index_reason"] == "FILE_INDEX_METADATA_UNAVAILABLE"
    assert data["files"][0]["index_state"] == "UNKNOWN"
    assert "synthetic private failure" not in json.dumps(data)


async def test_index_record_projection_is_one_bounded_parameterized_metadata_query():
    data = SimpleNamespace(execute=AsyncMock(return_value={"rows": [{
        "root_key": "notes", "relative_path": "quote'file.md", "active_version": "b" * 64,
        "source_hash": "a" * 64, "version_hash": "a" * 64, "version_state": "READY",
        "size_bytes": 4, "mtime_ns": 2,
    }]}))
    store = KnowledgeStore(data)
    result = await store.workspace_index_records([("notes", "quote'file.md")])
    assert result[("notes", "quote'file.md")]["active_version"] == "b" * 64
    statement, parameters = data.execute.await_args.args
    assert "quote'file.md" not in statement
    assert parameters == [["notes"], ["quote'file.md"]]
    assert "knowledge_chunks" not in statement
    assert "v.state" in statement and "s.active_version" in statement
    assert await store.workspace_index_records([]) == {}
    data.execute.assert_awaited_once()
    data.execute.return_value = {"rows": None}
    with pytest.raises(RuntimeError):
        await store.workspace_index_records([("notes", "note.md")])


async def test_large_unicode_inventory_is_batched_without_truncating_metadata():
    files = [("notes", str(index) + "😀" * 1000 + ".md") for index in range(70)]
    calls = []

    async def execute(statement, parameters):
        calls.append(parameters)
        request = json.dumps({"statement": statement, "parameters": parameters}, ensure_ascii=True)
        assert len(request.encode("utf-8")) < 512 * 1024
        rows = [{"root_key": root, "relative_path": path, "active_version": "b" * 64,
                 "source_hash": "a" * 64, "version_hash": "a" * 64, "version_state": "READY",
                 "size_bytes": 4, "mtime_ns": 2}
                for root, path in zip(*parameters, strict=True)]
        assert len(json.dumps({"rows": rows}, ensure_ascii=True).encode("utf-8")) < 512 * 1024
        return {"rows": rows}

    store = KnowledgeStore(SimpleNamespace(execute=execute))
    result = await store.workspace_index_records(files)
    assert set(result) == set(files)
    assert len(calls) > 1
    assert [pair for parameters in calls for pair in zip(*parameters, strict=True)] == files
