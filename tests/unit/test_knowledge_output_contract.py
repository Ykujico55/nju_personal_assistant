"""Synthetic knowledge hits must pass the same Gateway as a real Agent call."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from personal_knowledge import EXTENSION_VERSION
from personal_knowledge.models import (
    ChunkRecord,
    EvidenceStatus,
    Locator,
    SearchOutcome,
    SearchResult,
)
from personal_knowledge.worker import PersonalKnowledgeExtension

from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.tools import ToolGateway, ToolPolicy, ToolRegistry
from personal_assistant.domain import RiskLevel, ToolCall, ToolDescriptor, ToolOutcomeKind

ROOT = Path(__file__).resolve().parents[2] / "extensions" / "personal_knowledge"
BODY = "synthetic knowledge source body"


async def invoke_hit(locator, status, *, empty=False):
    record = ChunkRecord(
        source_id="fixture-source", version_id="fixture-version", ordinal=0, text=BODY,
        locator=locator, heading_path=(), content_hash="a" * 64, root_key="fixture",
        relative_path="note.txt", media_type="text/plain", source_hash="b" * 64,
    )
    extension = PersonalKnowledgeExtension()
    extension._index = SimpleNamespace(
        search=AsyncMock(return_value=SearchOutcome(
            results=() if empty else (record,), scores=() if empty else (0.5,),
            unknown=empty, vector_mode="disabled",
        )),
        evaluate=AsyncMock(return_value=[] if empty else [SearchResult(record, status, 0.5)]),
    )

    class Executor:
        async def execute(self, descriptor, arguments, context):
            result = await extension._invoke_search(arguments)
            return result.output

    registry = ToolRegistry()
    registry.publish((ToolDescriptor(
        id="knowledge.search", version=EXTENSION_VERSION, extension_id="personal.knowledge",
        extension_version=EXTENSION_VERSION, risk=RiskLevel.READ,
        input_schema=json.loads((ROOT / "schemas/search-input.json").read_text("utf-8")),
        output_schema=json.loads((ROOT / "schemas/search-output.json").read_text("utf-8")),
    ),))
    gateway = ToolGateway(
        registry=registry, policy=ToolPolicy(),
        approvals=ApprovalService(InMemoryApprovalRepository()), executor=Executor(),
    )
    return await gateway.invoke(ToolCall(
        tool_id="knowledge.search", tool_version=EXTENSION_VERSION,
        arguments={"query": "synthetic"}, task_id="fixture-task",
        workflow_allowed_tools=frozenset({"knowledge.search"}),
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(EvidenceStatus))
@pytest.mark.parametrize("locator", [
    Locator(kind="line_range", start=2, end=3),
    Locator(kind="page_char_range", start=0, end=8, page=2),
])
async def test_nonempty_search_passes_gateway_and_preserves_citation_privacy(locator, status):
    outcome = await invoke_hit(locator, status)
    assert outcome.kind is ToolOutcomeKind.SUCCESS, outcome.message
    item = outcome.result["results"][0]
    assert item["source_uri"] == "knowledge://fixture/note.txt"
    assert item["source_version"] == "fixture-version"
    assert item["extension_version"] == EXTENSION_VERSION
    assert item["status"] == status.value
    assert item["text"] == (BODY if status is EvidenceStatus.CURRENT else "")
    assert outcome.result["unknown"] is (status is not EvidenceStatus.CURRENT)
    if locator.page is None:
        assert "page" not in item
        assert (item["line_start"], item["line_end"]) == (2, 3)
    else:
        assert item["page"] == 2
        assert "line_start" not in item and "line_end" not in item
    if status is not EvidenceStatus.CURRENT:
        assert BODY not in json.dumps(outcome.result)


@pytest.mark.asyncio
async def test_empty_search_is_successful_and_explicitly_unknown():
    outcome = await invoke_hit(Locator(kind="line_range", start=1, end=1),
                               EvidenceStatus.CURRENT, empty=True)
    assert outcome.kind is ToolOutcomeKind.SUCCESS
    assert outcome.result == {"unknown": True, "vector_mode": "disabled", "results": []}
