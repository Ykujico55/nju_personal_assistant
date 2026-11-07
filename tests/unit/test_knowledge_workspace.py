from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from personal_assistant_sdk import ContextQuery
from personal_knowledge import EXTENSION_VERSION
from personal_knowledge.worker import PersonalKnowledgeExtension


def extension(config):
    item = PersonalKnowledgeExtension()
    item._runtime = SimpleNamespace(
        non_secret_config=config,
        extension_id="personal.knowledge",
        extension_version=EXTENSION_VERSION,
        host_data=None,
    )
    return item


async def test_workspace_lists_configured_files_without_index_or_body_reads(tmp_path, monkeypatch):
    (tmp_path / "数据结构.md").write_text("private body", encoding="utf-8")
    (tmp_path / "简历.pdf").write_bytes(b"not a parsed PDF")
    (tmp_path / "sample.bin").write_bytes(b"binary")
    worker = extension({"roots": [{"path": str(tmp_path), "key": "notes", "label": "学习资料"}]})
    worker._require_index = Mock(side_effect=AssertionError("overview must not access index"))
    monkeypatch.setattr(type(tmp_path), "read_bytes", Mock(side_effect=AssertionError("no body")))
    result = await worker.retrieve(
        ContextQuery(
            "知识库里有哪些文件", limit=5, filters={"provider_ids": ["knowledge.workspace"]}
        )
    )
    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["configured"] is True
    assert data["roots"][0]["state"] == "AVAILABLE"
    assert {f["relative_path"] for f in data["files"]} == {"数据结构.md", "简历.pdf", "sample.bin"}
    assert all(f["index_state"] == "UNKNOWN" for f in data["files"])
    assert "private body" not in result[0].text
    assert str(tmp_path) not in result[0].text
    assert result[0].metadata["sensitivity"] == "SENSITIVE"
    assert result[0].metadata["trust"] == "USER_SOURCE"
    assert result[0].metadata["extension_version"] == EXTENSION_VERSION
    assert result[0].metadata["status"] == "CURRENT"
    assert result[0].metadata["snapshot_kind"] == "workspace_metadata"
    assert not worker._require_index.called


async def test_missing_root_stays_configured_instead_of_asking_for_path(tmp_path):
    worker = extension({"roots": [{"path": str(tmp_path / "removed"), "key": "saved"}]})
    result = await worker.retrieve(
        ContextQuery("workspace", filters={"provider_ids": ["knowledge.workspace"]})
    )
    data = json.loads(result[0].text)
    assert data["configured"] is True
    assert data["roots"][0]["state"] == "UNAVAILABLE"
    assert data["roots"][0]["root_key"] == "saved"
    assert data["files"] == []
    assert data["complete"] is False


async def test_unconfigured_and_invalid_config_are_distinct():
    for config, expected in (({}, "NOT_CONFIGURED"), ({"roots": [{"path": 5}]}, "INVALID")):
        result = await extension(config).retrieve(
            ContextQuery("workspace", filters={"provider_ids": ["knowledge.workspace"]})
        )
        data = json.loads(result[0].text)
        assert data["configuration_state"] == expected
        assert data["files"] == []


async def test_workspace_and_search_share_one_total_limit_and_strip_dispatch_filter(tmp_path):
    worker = extension({"roots": [{"path": str(tmp_path)}]})
    index = SimpleNamespace(
        index_status=AsyncMock(return_value={
            "index_state": "READY", "index_reason": "INDEX_READY",
        }),
        search=AsyncMock(return_value=SimpleNamespace(results=(), scores=())),
        evaluate=AsyncMock(return_value=[]),
    )
    worker._require_index = Mock(return_value=index)
    result = await worker.retrieve(
        ContextQuery(
            "data", limit=1, filters={"provider_ids": ["knowledge.workspace", "knowledge.retrieve"]}
        )
    )
    assert len(result) == 1
    assert not index.search.called
    result = await worker.retrieve(
        ContextQuery(
            "data",
            limit=3,
            filters={
                "provider_ids": ["knowledge.workspace", "knowledge.retrieve"],
                "root_keys": ["root-1"],
            },
        )
    )
    index.search.assert_awaited_once_with("data", limit=2, filters={"root_keys": ["root-1"]})
    assert len(result) == 1


async def test_old_retrieval_and_probe_remain_compatible(tmp_path):
    worker = extension({"roots": [{"path": str(tmp_path)}]})
    index = SimpleNamespace(
        index_status=AsyncMock(return_value={
            "index_state": "READY", "index_reason": "INDEX_READY",
        }),
        search=AsyncMock(return_value=SimpleNamespace(results=(), scores=())),
        evaluate=AsyncMock(return_value=[]),
    )
    worker._require_index = Mock(return_value=index)
    assert await worker.retrieve(ContextQuery("", limit=0)) == ()
    assert not worker._require_index.called
    assert await worker.retrieve(ContextQuery("keyword", limit=2)) == ()
    index.search.assert_awaited_once_with("keyword", limit=2, filters=None)


async def test_no_arbitrary_path_or_unknown_provider_can_be_supplied(tmp_path):
    worker = extension({"roots": [{"path": str(tmp_path)}]})
    worker._require_index = Mock(side_effect=AssertionError("must reject first"))
    for filters in (
        {"provider_ids": ["other.provider"]},
        {"provider_ids": ["knowledge.workspace"], "path": "C:/"},
        {"provider_ids": "knowledge.workspace"},
    ):
        with pytest.raises(ValueError):
            await worker.retrieve(ContextQuery("workspace", filters=filters))
    assert not worker._require_index.called


def test_metadata_walk_is_bounded_and_reports_incomplete(tmp_path):
    from personal_knowledge.workspace import workspace_snapshot

    for index in range(8):
        (tmp_path / f"file-{index}.txt").write_text("body")
    snapshot = workspace_snapshot({"roots": [{"path": str(tmp_path)}]}, max_files=2, max_entries=3)
    assert len(snapshot["files"]) == 2
    assert snapshot["truncation_reasons"] == ["FILE_LIMIT"]
    assert snapshot["truncated"] is True
    assert snapshot["complete"] is False


def test_outside_links_and_ignored_directories_are_not_listed(tmp_path, monkeypatch):
    from personal_knowledge import workspace

    root = tmp_path / "authorized"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "hidden.txt").write_text("hidden")
    linked = root / "junction"
    linked.mkdir()
    (linked / "outside.txt").write_text("outside")
    monkeypatch.setattr(workspace, "_is_junction", lambda path: path.name == "junction")
    snapshot = workspace.workspace_snapshot({"roots": [{"path": str(root)}]})
    assert snapshot["files"] == []


def test_depth_time_and_changed_root_fail_closed(tmp_path, monkeypatch):
    from personal_knowledge import workspace

    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.txt").write_text("body")
    result = workspace.workspace_snapshot({"roots": [{"path": str(tmp_path)}]}, max_depth=0)
    assert result["files"] == []
    assert result["truncated"] is True
    assert result["complete"] is False
    assert result["truncation_reasons"] == ["DEPTH_LIMIT"]
    ticks = iter([0.0, 3.0, 3.0, 3.0])
    monkeypatch.setattr(workspace.time, "monotonic", lambda: next(ticks, 3.0))
    result = workspace.workspace_snapshot({"roots": [{"path": str(tmp_path)}]}, max_seconds=1.0)
    assert result["files"] == []
    assert result["truncated"] is True
    assert result["complete"] is False
    assert result["truncation_reasons"] == ["SCAN_TIMEOUT"]


@pytest.mark.parametrize(("file_count", "name_width"), [(100, 8), (45, 110)])
async def test_default_workspace_keeps_full_file_metadata_beyond_old_output_caps(
    tmp_path, monkeypatch, file_count, name_width
):
    from personal_knowledge import workspace

    names = {f"{index:03d}-{'x' * name_width}.txt" for index in range(file_count)}
    for name in names:
        (tmp_path / name).write_text("private body", encoding="utf-8")
    # Isolate output policy from machine-dependent filesystem timing. Runtime
    # deadline behavior has its own deterministic regression below.
    monkeypatch.setattr(workspace.time, "monotonic", lambda: 0.0)
    worker = extension({"roots": [{"path": str(tmp_path), "key": "notes"}]})
    worker._require_index = Mock(side_effect=AssertionError("overview must not access index"))
    result = await worker.retrieve(
        ContextQuery("list files", filters={"provider_ids": ["knowledge.workspace"]})
    )
    assert len(result) == 1
    data = json.loads(result[0].text)
    assert {item["relative_path"] for item in data["files"]} == names
    assert data["complete"] is True
    assert data["truncated"] is False
    assert data["truncation_reasons"] == []
    assert len(result[0].text) > 10000
    assert "private body" not in result[0].text
    assert not worker._require_index.called


def test_entry_scan_budget_still_marks_partial_without_default_file_cap(tmp_path):
    from personal_knowledge.workspace import workspace_snapshot

    for index in range(10):
        (tmp_path / f"file-{index}.txt").write_text("body")
    snapshot = workspace_snapshot({"roots": [{"path": str(tmp_path)}]}, max_entries=3)
    assert len(snapshot["files"]) == 3
    assert snapshot["truncation_reasons"] == ["ENTRY_SCAN_LIMIT"]
    assert snapshot["truncated"] is True
    assert snapshot["complete"] is False
