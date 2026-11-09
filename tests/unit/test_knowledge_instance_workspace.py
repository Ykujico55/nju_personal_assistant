from dataclasses import replace

import pytest
from personal_assistant_sdk import ContextQuery, RuntimeContext
from personal_knowledge.documents import read_document
from personal_knowledge.worker import PersonalKnowledgeExtension

from tests.unit.fake_knowledge_store import FakeKnowledgeStore


def runtime(root, revision="rev-1"):
    return RuntimeContext(
        "1",
        "personal.knowledge",
        "test",
        "ext_knowledge",
        non_secret_config={"workspace_binding": "instance"},
        host_data=object(),
        workspace={
            "workspace_id": "instance",
            "revision": revision,
            "roots": [{"key": "notes", "label": "Notes", "path": str(root), "mode": "read_write"}],
        },
    )


async def test_overview_and_document_use_same_workspace_without_plugin_roots(tmp_path):
    (tmp_path / "note.md").write_text("shared text", encoding="utf-8")
    worker = PersonalKnowledgeExtension()
    bound = runtime(tmp_path)
    worker._runtime = bound
    assert read_document(bound, "knowledge://notes/note.md")["text"] == "shared text"
    result = await worker.retrieve(
        ContextQuery("workspace", filters={"provider_ids": ["knowledge.workspace"]})
    )
    assert "note.md" in result[0].text
    assert str(tmp_path) not in result[0].text


async def test_workspace_switch_back_cannot_reuse_prior_index_binding(tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text("shared keyword", encoding="utf-8")
    store = FakeKnowledgeStore()
    monkeypatch.setattr("personal_knowledge.worker.KnowledgeStore", lambda _: store)
    previous = PersonalKnowledgeExtension()
    previous._runtime = runtime(tmp_path)
    index = previous._require_index()
    await index.reconcile()
    assert (await index.search("keyword")).results
    current = PersonalKnowledgeExtension()
    current._runtime = runtime(tmp_path, "rev-3")
    assert (await current._workspace_index_status())["index_state"] == "PENDING"
    assert (await current._require_index().search("keyword")).results == ()
    assert store.chunks
    await current._require_index().reconcile()
    assert (await current._workspace_index_status())["index_state"] == "READY"
    assert (await current._require_index().search("keyword")).results


async def test_missing_workspace_is_unavailable_not_legacy_fallback(tmp_path):
    worker = PersonalKnowledgeExtension()
    bound = replace(runtime(tmp_path), workspace=None)
    worker._runtime = bound
    assert not (await worker.health()).healthy
    assert read_document(bound, "knowledge://notes/note.md")["status"] == "WORKSPACE_UNAVAILABLE"
    with pytest.raises(ValueError, match="WORKSPACE_UNAVAILABLE"):
        worker._require_index()
