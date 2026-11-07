"""Index binding codec and complete scans with genuinely empty text files."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from personal_knowledge.binding import INDEX_BINDING_KEY, read_index_status
from personal_knowledge.models import ExtractionDiagnosticCode, ExtractionError
from personal_knowledge.store import KnowledgeStore

from personal_assistant.core.tools.schemas import validate_json_schema
from tests.unit.fake_knowledge_store import FakeKnowledgeStore
from tests.unit.test_knowledge_binding import make_index


class JsonCodecData:
    """Mimic the host json.dumps/json.loads codec, not a dict-only store."""

    def __init__(self):
        self.values = {}
        self.calls = []

    async def execute(self, statement, parameters):
        self.calls.append((statement, copy.deepcopy(parameters)))
        key = parameters[0]
        if statement.startswith("INSERT"):
            self.values[key] = json.loads(json.dumps(parameters[1]))
            return {"rowcount": 1, "rows": []}
        if statement.startswith("SELECT"):
            rows = [{"value": self.values[key]}] if key in self.values else []
            return {"rowcount": len(rows), "rows": rows}
        expected = parameters[1]
        legacy = parameters[3] if len(parameters) > 3 else object()
        if self.values.get(key) in (expected, legacy):
            self.values[key] = json.loads(json.dumps(parameters[2]))
            return {"rowcount": 1, "rows": [{"key": key}]}
        return {"rowcount": 0, "rows": []}


def binding(state="PENDING", scan="first"):
    return {"schema": 1, "binding_hash": "synthetic-root-hash", "scan_id": scan,
            "state": state}


async def test_meta_new_write_is_object_after_host_json_codec():
    data = JsonCodecData()
    store = KnowledgeStore(data)
    await store.set_meta(INDEX_BINDING_KEY, binding())
    assert isinstance(data.values[INDEX_BINDING_KEY], dict)
    assert await store.get_meta(INDEX_BINDING_KEY) == binding()
    assert await read_index_status(store, "synthetic-root-hash") == {
        "index_state": "PENDING", "index_reason": "INDEX_SCAN_INCOMPLETE"}


@pytest.mark.parametrize("state", ["PENDING", "READY"])
async def test_meta_legacy_string_is_decoded_once_without_changing_state(state):
    data = JsonCodecData()
    data.values[INDEX_BINDING_KEY] = json.dumps(binding(state), sort_keys=True)
    store = KnowledgeStore(data)
    assert await store.get_meta(INDEX_BINDING_KEY) == binding(state)
    expected = "READY" if state == "READY" else "PENDING"
    assert (await read_index_status(store, "synthetic-root-hash"))["index_state"] == expected
    assert (await read_index_status(store, "different-root"))["index_state"] == "PENDING"


@pytest.mark.parametrize("value", ["invalid json", "[]", "null", '"not an object"',
                                   json.dumps(json.dumps(binding("READY")))])
async def test_meta_malformed_or_recursively_encoded_state_is_not_ready(value):
    data = JsonCodecData()
    data.values[INDEX_BINDING_KEY] = value
    store = KnowledgeStore(data)
    assert await store.get_meta(INDEX_BINDING_KEY) is None
    assert (await read_index_status(store, "synthetic-root-hash"))["index_state"] == "PENDING"


@pytest.mark.parametrize("legacy", [False, True])
async def test_meta_compare_set_normalizes_legacy_and_rejects_stale_scan(legacy):
    data = JsonCodecData()
    original = binding()
    data.values[INDEX_BINDING_KEY] = (
        json.dumps(original, sort_keys=True) if legacy else original)
    store = KnowledgeStore(data)
    assert await store.compare_set_meta(INDEX_BINDING_KEY, original, binding("READY"))
    assert isinstance(data.values[INDEX_BINDING_KEY], dict)
    await store.set_meta(INDEX_BINDING_KEY, binding(scan="newer"))
    assert not await store.compare_set_meta(INDEX_BINDING_KEY, original, binding("READY"))
    assert await store.get_meta(INDEX_BINDING_KEY) == binding(scan="newer")


@pytest.mark.parametrize("empty", [b"", b" \r\n\t", b"\xef\xbb\xbf \n"])
async def test_empty_text_is_explicit_skip_without_blocking_healthy_index(tmp_path, empty):
    (tmp_path / "healthy.md").write_text("healthy searchable keyword", encoding="utf-8")
    (tmp_path / "empty.txt").write_bytes(empty)
    index = make_index(tmp_path, FakeKnowledgeStore())
    report = await index.reconcile(mode="full")
    assert not report.errors
    assert report.skipped_empty == 1
    assert [(item.path, item.code.value) for item in report.skipped] == [
        ("empty.txt", "EMPTY_FILE")]
    assert (report.added, report.modified, report.unchanged, report.versions_built) == (1, 0, 0, 1)
    assert (await index.index_status())["index_state"] == "READY"
    assert (await index.search("searchable")).results
    encoded = report.as_json()
    assert encoded["skipped_empty"] == 1
    assert encoded["skipped"][0]["path"] == "empty.txt"
    assert (encoded["index_state"], encoded["index_reason"]) == ("READY", "INDEX_READY")
    schema = json.loads((Path(__file__).resolve().parents[2] / "extensions" /
                         "personal_knowledge/schemas/reindex-output.json").read_text("utf-8"))
    validate_json_schema(encoded, schema)
    again = await index.reconcile(mode="full")
    assert (again.added, again.modified, again.unchanged, again.versions_built) == (0, 0, 1, 0)
    assert again.skipped_empty == 1 and not again.errors
    assert again.index_state == "READY"


async def test_full_reuses_existing_version_and_reports_unchanged(tmp_path):
    (tmp_path / "healthy.md").write_text("healthy searchable keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    await index.reconcile()
    saved = copy.deepcopy((store.sources, store.versions, store.chunks, store.events))
    report = await index.reconcile(mode="full")
    assert (report.modified, report.unchanged, report.versions_built) == (0, 1, 0)
    assert (store.sources, store.versions, store.chunks, store.events) == saved
    assert (await index.index_status())["index_state"] == "READY"


async def test_existing_document_becoming_empty_removes_old_chunks_before_ready(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("formercontent keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    await index.reconcile()
    assert (await index.search("formercontent")).results
    note.write_text("", encoding="utf-8")
    report = await index.reconcile(mode="full")
    assert not report.errors
    assert report.skipped_empty == 1
    assert report.modified == 0
    assert report.deleted == 1
    assert not store.sources and not store.chunks and not store.versions
    assert (await index.index_status())["index_state"] == "READY"
    assert not (await index.search("formercontent")).results


async def test_empty_source_delete_cas_loss_does_not_certify_ready(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("formercontent keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    await index.reconcile()
    before = copy.deepcopy(store.chunks)
    note.write_text("", encoding="utf-8")
    store.delete_source = AsyncMock(return_value=None)
    await index.reconcile()
    store.delete_source.assert_awaited_once()
    assert store.chunks == before
    assert (await index.index_status())["index_state"] == "PENDING"
    assert not (await index.search("formercontent")).results


async def test_extractor_empty_diagnostic_is_not_proof_of_empty_input(tmp_path, monkeypatch):
    (tmp_path / "nonempty.pdf").write_bytes(b"%PDF-1.4 synthetic scanned image")
    (tmp_path / "healthy.md").write_text("healthy keyword", encoding="utf-8")
    from personal_knowledge import index as index_module
    extract = index_module.extract_document

    def scanned_pdf(data, media_type, **kwargs):
        if media_type == "application/pdf":
            raise ExtractionError(ExtractionDiagnosticCode.EMPTY_FILE, "no OCR body")
        return extract(data, media_type, **kwargs)

    monkeypatch.setattr(index_module, "extract_document", scanned_pdf)
    index = make_index(tmp_path, FakeKnowledgeStore())
    report = await index.reconcile()
    assert report.skipped_empty == 0
    assert report.errors[0].code is ExtractionDiagnosticCode.EMPTY_FILE
    assert (await index.index_status())["index_state"] == "PENDING"


async def test_unexpected_no_chunks_from_nonempty_text_remains_pending(tmp_path, monkeypatch):
    (tmp_path / "nonempty.txt").write_text("not empty", encoding="utf-8")
    monkeypatch.setattr("personal_knowledge.index.chunk_document", lambda _: ())
    index = make_index(tmp_path, FakeKnowledgeStore())
    report = await index.reconcile()
    assert report.skipped_empty == 0
    assert report.errors[0].code is ExtractionDiagnosticCode.EMPTY_FILE
    assert (await index.index_status())["index_state"] == "PENDING"


async def test_report_reads_status_after_losing_binding_cas(tmp_path):
    (tmp_path / "healthy.md").write_text("healthy keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    index = make_index(tmp_path, store)
    compare = store.compare_set_meta

    async def newer_scan_wins(key, expected, value):
        await store.set_meta(key, dict(expected, scan_id="newer", state="PENDING"))
        return await compare(key, expected, value)

    store.compare_set_meta = newer_scan_wins
    report = await index.reconcile()
    assert report.versions_built == 1 and not report.errors
    assert (report.index_state, report.index_reason) == ("PENDING", "INDEX_SCAN_INCOMPLETE")
    assert not (await index.search("healthy")).results
