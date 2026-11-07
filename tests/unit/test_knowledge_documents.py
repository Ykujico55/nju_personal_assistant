"""Read authorized synthetic document bodies without indexing or host services."""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import quote

import pytest
from personal_assistant_sdk import ContextQuery
from personal_knowledge import EXTENSION_VERSION
from personal_knowledge.worker import PersonalKnowledgeExtension
from test_knowledge_extractors import build_pdf


def runtime(root, **config):
    return SimpleNamespace(
        non_secret_config={"roots": [{"key": "notes", "path": str(root)}], **config},
        extension_id="personal.knowledge",
        extension_version=EXTENSION_VERSION,
        host_data=None,
    )


def read(root, resource, **config):
    from personal_knowledge.documents import read_document

    return read_document(runtime(root, **config), resource)


@pytest.mark.parametrize("suffix", [".md", ".txt"])
def test_reads_whole_text_by_unique_name_without_index_and_does_not_truncate(tmp_path, suffix):
    name = "资料" + suffix
    body = "# 开始\n\n" + "合成正文\n" * 2400 + "最后一行"
    (tmp_path / name).write_text(body, encoding="utf-8")
    result = read(tmp_path, name)
    assert result["status"] == "READ" and result["complete"] is True
    assert result["text"] == body
    assert result["source_uri"] == "knowledge://notes/" + quote(name)
    assert result["locator"]["kind"] == "line_range"
    assert str(tmp_path) not in json.dumps(result, ensure_ascii=False)
    assert {item.name for item in tmp_path.iterdir()} == {name}


def test_exact_uri_selects_nested_resource_and_does_not_depend_on_index(tmp_path):
    directory = tmp_path / "章节"
    directory.mkdir()
    (directory / "note.txt").write_text("exact body", encoding="utf-8")
    result = read(tmp_path, "knowledge://notes/" + quote("章节/note.txt"))
    assert result["text"] == "exact body" and result["complete"]
    assert result["status"] == "READ"


def test_duplicate_filename_requires_selection_without_reading_bodies(tmp_path, monkeypatch):
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "same.txt").write_text("do not read this", encoding="utf-8")
    import personal_knowledge.documents as module

    monkeypatch.setattr(module, "_read_current", Mock(side_effect=AssertionError("no body read")))
    result = read(tmp_path, "same.txt")
    assert result["status"] == "AMBIGUOUS" and not result["complete"]
    assert {item["source_uri"] for item in result["candidates"]} == {
        "knowledge://notes/a/same.txt",
        "knowledge://notes/b/same.txt",
    }
    assert not result["text"]


@pytest.mark.parametrize(
    "resource",
    [
        "../outside.txt",
        "folder/note.txt",
        "C:/outside.txt",
        "https://example.invalid/note.txt",
        "knowledge://other/note.txt",
        "knowledge://notes/../outside.txt",
        "knowledge://notes/%2e%2e/outside.txt",
        "knowledge://notes/a%5c..%5cnote.txt",
        "knowledge://notes/note.txt?query=x",
        "knowledge://notes/note.txt#frag",
        "knowledge://user@notes/note.txt",
        "knowledge://notes/note.txt:stream",
        "knowledge://notes/%00.txt",
        "knowledge://notes/%GG.txt",
        "读取 note.txt 的内容",
    ],
)
def test_invalid_or_unauthorized_resource_never_reads_body(tmp_path, resource, monkeypatch):
    (tmp_path / "note.txt").write_text("do not read", encoding="utf-8")
    import personal_knowledge.documents as module

    monkeypatch.setattr(module, "_read_current", Mock(side_effect=AssertionError("no body read")))
    result = read(tmp_path, resource)
    assert result["status"] in {"RESOURCE_INVALID", "RESOURCE_NOT_ALLOWED", "NOT_FOUND"}
    assert result["complete"] is False and result["text"] == ""


@pytest.mark.parametrize("relative", [".private.txt", "node_modules/note.txt", ".hidden/note.txt"])
def test_explicit_uri_cannot_bypass_hidden_or_ignored_paths(tmp_path, relative):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hidden sentinel", encoding="utf-8")
    result = read(tmp_path, "knowledge://notes/" + relative)
    assert result["status"] == "RESOURCE_NOT_ALLOWED"
    assert "hidden sentinel" not in json.dumps(result)


def test_include_hidden_requires_explicit_configuration(tmp_path):
    (tmp_path / ".private.txt").write_text("authorized hidden", encoding="utf-8")
    result = read(tmp_path, "knowledge://notes/.private.txt", include_hidden=True)
    assert result["status"] == "READ" and result["text"] == "authorized hidden"


def test_unavailable_unsupported_and_too_large_are_explicit(tmp_path):
    assert read(tmp_path, "missing.txt")["status"] == "NOT_FOUND"
    (tmp_path / "unsupported.docx").write_bytes(b"unsupported body")
    assert read(tmp_path, "unsupported.docx")["status"] == "UNSUPPORTED_FORMAT"
    (tmp_path / "large.txt").write_bytes(b"a" * 1025)
    result = read(tmp_path, "large.txt", max_file_bytes=1024)
    assert result["status"] == "FILE_TOO_LARGE" and not result["text"]
    assert read(tmp_path / "gone", "note.txt")["status"] == "ROOT_UNAVAILABLE"


def test_lookup_budget_cannot_claim_unique_filename(tmp_path, monkeypatch):
    (tmp_path / "note.txt").write_text("not until proven unique", encoding="utf-8")
    import personal_knowledge.documents as module

    real = module.workspace_snapshot
    monkeypatch.setattr(
        module,
        "workspace_snapshot",
        lambda config: {
            **real(config),
            "complete": False,
            "truncated": True,
            "truncation_reasons": ["ENTRY_SCAN_LIMIT"],
        },
    )
    assert read(tmp_path, "note.txt")["status"] == "LOOKUP_INCOMPLETE"


def test_pdf_uses_existing_extractor_and_reports_incomplete_extraction(tmp_path, monkeypatch):
    (tmp_path / "sample.pdf").write_bytes(build_pdf(["BT (page one) Tj ET", "BT (page two) Tj ET"]))
    result = read(tmp_path, "sample.pdf")
    assert result["status"] == "READ" and "page one" in result["text"]
    assert "page two" in result["text"] and result["locator"] == {
        "kind": "page_range",
        "start": 1,
        "end": 2,
    }
    import personal_knowledge.documents as module
    from personal_knowledge.models import ExtractionDiagnostic, ExtractionDiagnosticCode

    monkeypatch.setattr(
        module,
        "extract_document",
        lambda *args, **kwargs: SimpleNamespace(
            text="partial sentinel",
            diagnostics=(
                ExtractionDiagnostic(
                    ExtractionDiagnosticCode.PDF_UNSUPPORTED_FILTER,
                    "raw diagnostic secret sentinel",
                ),
            ),
        ),
    )
    result = read(tmp_path, "sample.pdf")
    assert result["status"] == "EXTRACTION_INCOMPLETE" and not result["complete"]
    assert "sentinel" not in json.dumps(result)


def test_read_checks_file_identity_and_does_not_publish_changed_body(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    target.write_text("old body", encoding="utf-8")
    import personal_knowledge.documents as module

    original = module.extract_document

    def changed(data, *args, **kwargs):
        target.write_text("changed body with different size", encoding="utf-8")
        return original(data, *args, **kwargs)

    monkeypatch.setattr(module, "extract_document", changed)
    result = read(tmp_path, "note.txt")
    assert result["status"] == "FILE_CHANGED" and result["text"] == ""


def test_root_and_nested_junctions_are_not_followed(tmp_path, monkeypatch):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "note.txt").write_text("no link body", encoding="utf-8")
    import personal_knowledge.documents as module

    monkeypatch.setattr(module, "_is_junction", lambda path: path == tmp_path / "sub")
    assert read(tmp_path, "knowledge://notes/sub/note.txt")["status"] == "RESOURCE_NOT_ALLOWED"
    monkeypatch.setattr(module, "_is_junction", lambda path: path == tmp_path)
    assert read(tmp_path, "knowledge://notes/sub/note.txt")["status"] == "ROOT_UNAVAILABLE"


def test_internal_link_and_root_alias_never_read(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "note.txt").write_text("target sentinel", encoding="utf-8")
    link = tmp_path / "alias"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
        )
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        nested = read(tmp_path, "knowledge://notes/alias/note.txt")
        assert nested["status"] == "RESOURCE_NOT_ALLOWED" and not nested["text"]
        aliased_root = read(link, "knowledge://notes/note.txt")
        assert aliased_root["status"] == "ROOT_UNAVAILABLE" and not aliased_root["text"]
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


async def test_document_provider_and_read_tool_are_registered_and_index_free(tmp_path):
    (tmp_path / "note.txt").write_text("tool and context source", encoding="utf-8")
    worker = PersonalKnowledgeExtension()
    worker._runtime = runtime(tmp_path)
    worker._require_index = Mock(side_effect=AssertionError("no index access"))
    descriptor = next(item for item in worker.tools() if item.id == "knowledge.read")
    assert descriptor.risk.value == "READ"
    output = await worker.invoke("knowledge.read", {"resource": "note.txt"}, None)
    assert output.output["status"] == "READ"
    evidence = await worker.retrieve(
        ContextQuery(
            "note.txt",
            filters={"provider_ids": ["knowledge.document"]},
        )
    )
    assert len(evidence) == 1 and "tool and context source" in evidence[0].text
    assert evidence[0].source_uri == "knowledge://notes/note.txt"
    assert evidence[0].metadata["sensitivity"] == "SENSITIVE"
    assert evidence[0].metadata["trust"] == "USER_SOURCE"
    assert evidence[0].metadata["extension_version"] == "0.1.5"
    assert not worker._require_index.called
    assert await worker.retrieve(ContextQuery("", limit=0)) == ()


async def test_pending_body_search_returns_explicit_status_evidence(tmp_path):
    worker = PersonalKnowledgeExtension()
    worker._runtime = runtime(tmp_path)
    worker._index = SimpleNamespace(
        index_status=AsyncMock(
            return_value={
                "index_state": "PENDING",
                "index_reason": "INDEX_BINDING_MISSING",
            }
        ),
        search=AsyncMock(),
    )
    result = await worker.retrieve(
        ContextQuery("关键词", filters={"provider_ids": ["knowledge.retrieve"]})
    )
    assert len(result) == 1
    body = json.loads(result[0].text)
    assert body["index_state"] == "PENDING" and body["index_reason"] == "INDEX_BINDING_MISSING"
    assert body["kind"] == "knowledge_index_status"
    assert not worker._index.search.called


@pytest.mark.parametrize("body", ["ASCII" * 800, '中文"\n' * 500], ids=["ascii", "escaped"])
def test_rpc_budget_reports_too_large_without_partial_tool_or_context(tmp_path, monkeypatch, body):
    import personal_knowledge.documents as module

    (tmp_path / "large.txt").write_text(body, encoding="utf-8")
    monkeypatch.setattr(module, "MAX_DOCUMENT_RESPONSE_BYTES", 1024)
    result = read(tmp_path, "large.txt")
    assert result["status"] == "DOCUMENT_TOO_LARGE" and not result["complete"]
    assert result["text"] == ""
    evidence = module.document_evidence(runtime(tmp_path), "large.txt")
    assert json.loads(evidence.text)["status"] == "DOCUMENT_TOO_LARGE"
    assert body not in evidence.text


async def test_read_result_passes_gateway_and_real_sdk_response_codec(tmp_path):
    from dataclasses import asdict

    from personal_assistant_sdk.rpc import encode_frame

    from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
    from personal_assistant.core.tools import ToolGateway, ToolPolicy, ToolRegistry
    from personal_assistant.domain import RiskLevel, ToolCall, ToolDescriptor, ToolOutcomeKind

    (tmp_path / "note.txt").write_text('"quoted Chinese 正文"\n' * 4000, encoding="utf-8")
    worker = PersonalKnowledgeExtension()
    worker._runtime = runtime(tmp_path)
    sdk = next(item for item in worker.tools() if item.id == "knowledge.read")
    registry = ToolRegistry()
    registry.publish(
        (
            ToolDescriptor(
                id=sdk.id,
                version=EXTENSION_VERSION,
                extension_id="personal.knowledge",
                extension_version=EXTENSION_VERSION,
                risk=RiskLevel.READ,
                input_schema=sdk.input_schema,
                output_schema=sdk.output_schema,
            ),
        )
    )

    class Executor:
        async def execute(self, descriptor, arguments, context):
            return (await worker.invoke(descriptor.id, arguments, context)).output

    gateway = ToolGateway(
        registry=registry,
        policy=ToolPolicy(),
        approvals=ApprovalService(InMemoryApprovalRepository()),
        executor=Executor(),
    )
    result = await gateway.invoke(
        ToolCall(
            tool_id=sdk.id,
            tool_version=EXTENSION_VERSION,
            arguments={"resource": "note.txt"},
            task_id="synthetic-document",
            workflow_allowed_tools=frozenset({sdk.id}),
        )
    )
    assert result.kind is ToolOutcomeKind.SUCCESS, result.message
    assert result.result["status"] == "READ" and result.result["complete"]
    assert (
        len(encode_frame({"jsonrpc": "2.0", "id": "one", "result": result.result}))
        < 4 * 1024 * 1024
    )
    evidence = await worker.retrieve(
        ContextQuery(
            "note.txt",
            filters={
                "provider_ids": ["knowledge.document"],
            },
        )
    )
    assert (
        len(encode_frame({"jsonrpc": "2.0", "id": "two", "result": [asdict(evidence[0])]}))
        < 4 * 1024 * 1024
    )


def test_ignored_path_case_cannot_bypass_platform_directory_policy(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "note.txt").write_text("ignored sentinel", encoding="utf-8")
    name = "NODE_MODULES" if os.name == "nt" else "node_modules"
    result = read(tmp_path, f"knowledge://notes/{name}/note.txt")
    assert result["status"] == "RESOURCE_NOT_ALLOWED" and not result["text"]


async def test_real_dispatcher_returns_explicit_oversize_status_for_both_slots(tmp_path):
    from personal_assistant_sdk.rpc import RpcRequest, encode_frame
    from personal_assistant_sdk.worker import ExtensionDispatcher

    (tmp_path / "expanded.txt").write_text(
        "start" + "\t" * (3 * 1024 * 1024) + "end", encoding="utf-8"
    )
    worker = PersonalKnowledgeExtension()
    worker._runtime = runtime(tmp_path)
    dispatcher = ExtensionDispatcher(lambda: worker)
    dispatcher._extension, dispatcher._initialized = worker, True
    tool = await dispatcher.dispatch(
        RpcRequest(
            "read",
            "tool.invoke",
            {
                "tool_id": "knowledge.read",
                "arguments": {"resource": "expanded.txt"},
                "context": {
                    "task_id": "synthetic",
                    "run_id": "synthetic",
                    "deadline": "2099-01-01T00:00:00Z",
                    "idempotency_key": "read",
                },
            },
        )
    )
    assert tool.error is None, tool.error
    assert tool.result["output"]["status"] == "DOCUMENT_TOO_LARGE"
    assert tool.result["output"]["text"] == ""
    context = await dispatcher.dispatch(
        RpcRequest(
            "context",
            "context.retrieve",
            {
                "query": {
                    "text": "expanded.txt",
                    "filters": {"provider_ids": ["knowledge.document"]},
                },
            },
        )
    )
    assert context.error is None, context.error
    assert json.loads(context.result[0]["text"])["status"] == "DOCUMENT_TOO_LARGE"
    assert len(encode_frame(tool)) < 4096 and len(encode_frame(context)) < 4096


def test_basename_does_not_enumerate_through_parent_junction(tmp_path):
    parent = tmp_path / "target"
    root = parent / "vault"
    root.mkdir(parents=True)
    for folder in ("a", "b"):
        (root / folder).mkdir()
        (root / folder / "same.txt").write_text("private sentinel", encoding="utf-8")
    link = tmp_path / "alias"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(parent)], check=True, capture_output=True
        )
    else:
        link.symlink_to(parent, target_is_directory=True)
    try:
        result = read(link / "vault", "same.txt")
        assert result["status"] == "ROOT_UNAVAILABLE"
        assert not result.get("candidates") and result["text"] == ""
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


async def test_combined_provider_response_never_exceeds_actual_rpc_budget(tmp_path, monkeypatch):
    import personal_knowledge.worker as module
    from personal_assistant_sdk.rpc import RpcRequest
    from personal_assistant_sdk.worker import ExtensionDispatcher
    from personal_knowledge.documents import status_evidence

    (tmp_path / "combined.txt").write_text(
        "document-body-" + "a" * (2 * 1024 * 1024), encoding="utf-8"
    )
    worker = PersonalKnowledgeExtension()
    worker._runtime = runtime(tmp_path)
    overview = status_evidence(
        worker._runtime,
        {
            "kind": "synthetic_metadata",
            "value": "workspace-body-" + "b" * (2 * 1024 * 1024),
        },
        source_id="knowledge.workspace",
    )
    monkeypatch.setattr(module, "workspace_evidence", lambda *args, **kwargs: overview)
    dispatcher = ExtensionDispatcher(lambda: worker)
    dispatcher._extension, dispatcher._initialized = worker, True
    response = await dispatcher.dispatch(
        RpcRequest(
            "combined",
            "context.retrieve",
            {
                "query": {
                    "text": "combined.txt",
                    "filters": {
                        "provider_ids": ["knowledge.document", "knowledge.workspace"],
                    },
                },
            },
        )
    )
    assert response.error is None, response.error
    assert len(response.result) == 1
    output = json.loads(response.result[0]["text"])
    assert output["status"] == "DOCUMENT_TOO_LARGE" and not output["complete"]
    assert "document-body-" not in response.result[0]["text"]
    assert "workspace-body-" not in response.result[0]["text"]
