"""File discovery and tool schemas reach the real planner without a live model."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

import pytest
from personal_knowledge import EXTENSION_VERSION
from personal_knowledge.documents import read_document
from personal_knowledge.workspace import workspace_evidence, workspace_snapshot

from personal_assistant.core.agent.planner import StructuredPlanner
from personal_assistant.core.extensions import ManifestParser
from personal_assistant.core.extensions.descriptors import manifest_tool_descriptors
from personal_assistant.core.models import ModelOutput
from personal_assistant.core.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[2] / "extensions/personal_knowledge"


def runtime(root):
    return SimpleNamespace(
        non_secret_config={"roots": [{"path": str(root), "key": "MyNotes"}]},
        extension_id="personal.knowledge", extension_version=EXTENSION_VERSION,
        host_data=None,
    )


def test_workspace_file_source_uri_is_exact_and_can_be_read_without_index(tmp_path, monkeypatch):
    name = " leading 空格#100%.md"
    relative = "nested/" + name
    (tmp_path / "nested").mkdir()
    (tmp_path / relative).write_text("synthetic private body", encoding="utf-8")
    context = runtime(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", Mock(side_effect=AssertionError("metadata only")))
        snapshot = workspace_snapshot(context.non_secret_config)
    assert len(snapshot["files"]) == 1
    file = snapshot["files"][0]
    assert file["source_uri"] == "knowledge://MyNotes/" + quote(relative, safe="/")
    assert file["relative_path"] == relative
    assert "synthetic private body" not in json.dumps(snapshot)
    assert str(tmp_path) not in json.dumps(snapshot)
    result = read_document(context, file["source_uri"])
    assert result["status"] == "READ"
    assert result["source_uri"] == file["source_uri"]
    assert result["text"] == "synthetic private body"


@pytest.mark.parametrize("index_status", [None, {"index_state": "PENDING"}])
def test_overview_reference_is_explicitly_not_a_file_resource(tmp_path, index_status):
    (tmp_path / "notes.md").write_text("synthetic private body", encoding="utf-8")
    context = runtime(tmp_path)
    evidence = workspace_evidence(context, index_status=index_status)
    snapshot = json.loads(evidence.text)
    assert evidence.source_uri == "knowledge-workspace://instance/overview"
    assert evidence.source_uri in snapshot["note"]
    assert "knowledge.read" in snapshot["note"]
    assert "不能" in snapshot["note"]
    assert "files[].source_uri" in snapshot["note"]
    assert read_document(context, evidence.source_uri)["status"] == "RESOURCE_INVALID"
    assert snapshot["files"][0]["source_uri"] != evidence.source_uri


async def test_read_resource_schema_reaches_registered_planner_request():
    manifest = ManifestParser().parse(ROOT)
    registry = ToolRegistry()
    registry.publish(manifest_tool_descriptors(manifest))

    class SyntheticRouter:
        provider_ids = frozenset({"synthetic"})

        def __init__(self):
            self.requests = []

        async def complete(self, request, **kwargs):
            self.requests.append(request)
            return ModelOutput(
                text='{ "kind": "NEEDS_USER", "message": "Which file?" }',
                provider_id="synthetic", model_id="fixture",
            )

    router = SyntheticRouter()
    planner = StructuredPlanner(router, "synthetic")
    result = await planner.propose(
        objective="Read a selected file", messages=[],
        tools=registry.snapshot().descriptors(), observations=[],
    )
    assert result == {"kind": "NEEDS_USER", "message": "Which file?"}
    assert len(router.requests) == 1
    payload = router.requests[0].instruction.split("Registered tools: ", 1)[1].split(
        " Reviewed scoped preferences", 1,
    )[0]
    read_tool = next(item for item in json.loads(payload) if item["id"] == "knowledge.read")
    resource = read_tool["input_schema"]["properties"]["resource"]
    assert "without indexing" in read_tool["input_schema"]["description"]
    assert "knowledge://" in resource["description"]
    assert "knowledge-workspace://" in resource["description"]
    assert "knowledge-status://" in resource["description"]
    assert "not" in resource["description"]
    assert "filename" in resource["description"]
    assert read_tool["input_schema"]["additionalProperties"] is False
    assert read_tool["risk"] == "READ"
    assert read_tool["version"] == manifest.version
