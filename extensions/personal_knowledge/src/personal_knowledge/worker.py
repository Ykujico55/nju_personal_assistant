"""Personal knowledge extension worker.

Registers the standard slots for authorized-root knowledge retrieval.  The
extension talks to PostgreSQL only through the host's generic data capability:
no credential, connection string or host path crosses the worker boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from personal_assistant_sdk import (
    PROTOCOL_VERSION,
    ContextQuery,
    DomainEvent,
    DrainReport,
    EventSourceDescriptor,
    Evidence,
    ExtensionInfo,
    FormSchemaDescriptor,
    HealthReport,
    InvocationContext,
    MigrationDescriptor,
    Outcome,
    PollRequest,
    PollResult,
    RiskLevel,
    RuntimeContext,
    ScheduleDefinition,
    ScheduleMisfirePolicy,
    ToolDescriptor,
    ToolResult,
    WorkspaceConfigurationError,
    workspace_config,
)
from personal_assistant_sdk.worker import run_stdio_worker

from . import EXTENSION_VERSION
from .binding import DEFAULT_MAX_FILE_BYTES, binding_fingerprint, read_index_status
from .documents import bounded_context, document_evidence, read_document, status_evidence
from .embedding import (
    DeterministicTestEmbeddingProvider,
    EmbeddingProvider,
    EmbeddingUnavailable,
    build_embedding_provider,
)
from .index import KnowledgeIndex
from .indexing import IndexBuildError, build_index
from .models import (
    ChunkRecord,
    EvidenceStatus,
    ReconcileReport,
    SearchResult,
)
from .paths import (
    AuthorizedRoot,
    RootConfigurationError,
    parse_roots,
    resolve_roots,
)
from .store import KnowledgeStore
from .workspace import runtime_workspace_snapshot, workspace_evidence

EVENT_SOURCE_ID = "knowledge.file_changes"
DEFAULT_POLL_LIMIT = 100
MAX_POLL_LIMIT = 500
SENSITIVITY = "PERSONAL"
TRUST = "USER_SOURCE"


def _package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_json(reference: str) -> dict[str, Any]:
    data = json.loads((_package_root() / reference).read_text("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{reference} must contain a JSON object")
    return data


def load_migrations() -> tuple[dict[str, Any], ...]:
    directory = _package_root() / "migrations"
    descriptors: list[dict[str, Any]] = []
    if not directory.is_dir():
        return ()
    for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        match = path.name.split("_", 1)[0]
        descriptors.append(
            {
                "version": int(match),
                "path": f"migrations/{path.name}",
                "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
                "description": f"personal knowledge schema {path.stem}",
            }
        )
    return tuple(descriptors)


MIGRATIONS = load_migrations()


class PersonalKnowledgeExtension:
    def __init__(self) -> None:
        self._runtime: RuntimeContext | None = None
        self._index: KnowledgeIndex | None = None
        self._draining = False
        self._indexing_task: asyncio.Task[None] | None = None
        self._indexing_error: str | None = None

    # -- Extension protocol -------------------------------------------------

    async def initialize(self, runtime: RuntimeContext) -> ExtensionInfo:
        self._runtime = runtime
        if runtime.host_data is not None and runtime.non_secret_config:
            self._indexing_task = asyncio.create_task(self._automatic_index())
        return ExtensionInfo(
            id=runtime.extension_id,
            version=runtime.extension_version,
            protocol_version=PROTOCOL_VERSION,
            slots=(
                "ToolProvider",
                "ContextProvider",
                "EventSource",
                "ScheduleProvider",
                "MigrationProvider",
                "FormSchemaProvider",
            ),
            schema_hash=runtime.manifest_schema_hash,
        )

    async def health(self) -> HealthReport:
        if self._runtime is None or self._draining:
            return HealthReport(healthy=False, status="draining")
        try:
            self._authorized_roots()
        except WorkspaceConfigurationError as exc:
            return HealthReport(
                healthy=False, status="workspace_unavailable", details={"code": exc.code}
            )
        except RootConfigurationError as exc:
            if exc.code in {"roots_invalid", "roots_empty", "roots_too_many"}:
                return HealthReport(healthy=True, status="awaiting_configuration")
            return HealthReport(
                healthy=False, status="config_invalid", details={"code": exc.code}
            )
        try:
            config = workspace_config(self._runtime, include_modes=False)
            build_embedding_provider(config.get("embedding"))
            max_file_bytes = config.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)
            if (
                isinstance(max_file_bytes, bool)
                or not isinstance(max_file_bytes, int)
                or not 1024 <= max_file_bytes <= 100 * 1024 * 1024
            ):
                raise ValueError("max_file_bytes is out of range")
            if not isinstance(config.get("include_hidden", False), bool):
                raise ValueError("include_hidden must be a boolean")
        except EmbeddingUnavailable as exc:
            return HealthReport(
                healthy=False, status="config_invalid", details={"code": exc.code}
            )
        except ValueError:
            return HealthReport(
                healthy=False,
                status="config_invalid",
                details={"code": "KNOWLEDGE_CONFIG_INVALID"},
            )
        details = {}
        if self._indexing_task is not None and not self._indexing_task.done():
            details["index_state"] = "INDEXING"
        elif self._indexing_error is not None:
            details.update(index_state="FAILED", code=self._indexing_error)
        return HealthReport(healthy=True, status="ready", details=details)

    async def drain(self, deadline: float) -> DrainReport:
        del deadline
        self._draining = True
        await self._stop_indexing()
        return DrainReport(drained=True, active_calls=0)

    async def shutdown(self) -> None:
        self._draining = True
        await self._stop_indexing()
        if self._index is not None:
            await self._index.embedder.aclose()
        self._index = None
        self._runtime = None

    async def _automatic_index(self) -> None:
        try:
            await build_index(self._require_index(), automatic=True)
        except asyncio.CancelledError:
            raise
        except IndexBuildError as exc:
            self._indexing_error = str(exc)
        except Exception:
            self._indexing_error = "INDEX_BUILD_FAILED"

    async def _stop_indexing(self) -> None:
        task, self._indexing_task = self._indexing_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # -- ToolProvider -------------------------------------------------------

    def tools(self) -> tuple[ToolDescriptor, ...]:
        return (
            ToolDescriptor(
                id="knowledge.search",
                risk=RiskLevel.READ,
                description="Search the authorized personal knowledge index.",
                input_schema=_load_json("schemas/search-input.json"),
                output_schema=_load_json("schemas/search-output.json"),
            ),
            ToolDescriptor(
                id="knowledge.read",
                risk=RiskLevel.READ,
                description=("Read one complete authorized TXT/Markdown/PDF document "
                             "without indexing. "
                             "resource must be its exact knowledge://root/relative URI or unique "
                             "filename including extension; ambiguous names return candidates."),
                input_schema=_load_json("schemas/read-input.json"),
                output_schema=_load_json("schemas/read-output.json"),
            ),
            ToolDescriptor(
                id="knowledge.reindex",
                risk=RiskLevel.INTERNAL_WRITE,
                description="Scan authorized roots and rebuild changed index versions.",
                input_schema=_load_json("schemas/reindex-input.json"),
                output_schema=_load_json("schemas/reindex-output.json"),
            ),
        )

    async def invoke(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        context: InvocationContext,
    ) -> ToolResult:
        del context
        if tool_id == "knowledge.search":
            return await self._invoke_search(arguments)
        if tool_id == "knowledge.read":
            if set(arguments) != {"resource"}:
                raise ValueError("read requires exactly one resource")
            return ToolResult(
                outcome=Outcome.SUCCEEDED,
                output=read_document(self._require_runtime(), arguments["resource"]),
            )
        if tool_id == "knowledge.reindex":
            return await self._invoke_reindex(arguments)
        raise ValueError(f"unknown tool: {tool_id}")

    async def _invoke_search(self, arguments: Mapping[str, Any]) -> ToolResult:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        limit = arguments.get("limit", 5)
        filters = arguments.get("filters")
        index = self._require_index()
        outcome = await index.search(query, limit=_search_limit(limit), filters=_filters(filters))
        evaluated = await index.evaluate(outcome.results, outcome.scores)
        results = [_result_view(item, self._runtime) for item in evaluated]
        has_current = any(item.status is EvidenceStatus.CURRENT for item in evaluated)
        output: dict[str, Any] = {
            "unknown": not has_current,
            "vector_mode": outcome.vector_mode,
            "results": results,
        }
        return ToolResult(outcome=Outcome.SUCCEEDED, output=output)

    async def _invoke_reindex(self, arguments: Mapping[str, Any]) -> ToolResult:
        mode = arguments.get("mode", "incremental")
        if mode not in {"incremental", "full"}:
            raise ValueError("mode must be 'incremental' or 'full'")
        index = self._require_index()
        try:
            report = await build_index(index, automatic=False, mode=str(mode))
        except IndexBuildError as exc:
            return ToolResult(outcome=Outcome.NEEDS_USER_ACTION, output={"code": str(exc)})
        assert report is not None
        self._indexing_error = None
        return ToolResult(outcome=Outcome.SUCCEEDED, output=_report_view(report))

    # -- ContextProvider ----------------------------------------------------

    async def retrieve(self, query: ContextQuery) -> tuple[Evidence, ...]:
        if not query.text.strip() or query.limit <= 0:
            # Install-time contract verification probes with an empty query and
            # must never touch the database or filesystem.
            return ()
        limit = _search_limit(query.limit)
        raw_filters = dict(query.filters)
        selected = raw_filters.pop("provider_ids", ["knowledge.retrieve"])
        if (not isinstance(selected, (list, tuple)) or not selected
                or any(not isinstance(value, str) or value not in {
                    "knowledge.retrieve", "knowledge.workspace", "knowledge.document"}
                       for value in selected)):
            raise ValueError("unknown or invalid context provider selection")
        filters = _filters(raw_filters or None)
        evidence: list[Evidence] = []
        if "knowledge.document" in selected:
            if filters:
                raise ValueError("document retrieval uses the exact resource, not search filters")
            evidence.append(document_evidence(self._require_runtime(), query.text))
        if "knowledge.workspace" in selected and len(evidence) < limit:
            if filters and set(filters) - {"root_keys"}:
                raise ValueError("workspace overview accepts only configured root keys")
            evidence.append(await self._workspace_overview(
                root_keys=filters.get("root_keys") if filters else None,
            ))
        if "knowledge.retrieve" not in selected or len(evidence) >= limit:
            return bounded_context(self._require_runtime(), evidence)
        index = self._require_index()
        index_status = await index.index_status()
        if index_status["index_state"] != "READY":
            evidence.append(status_evidence(
                self._require_runtime(),
                {"kind": "knowledge_index_status", **index_status,
                 "note": "正文索引尚未就绪；这不是无命中或目录为空。指定文件可使用直接读取。"},
                source_id="knowledge.index-status",
            ))
            return bounded_context(self._require_runtime(), evidence)
        outcome = await index.search(
            query.text,
            limit=limit - len(evidence),
            filters=filters,
        )
        for item in await index.evaluate(outcome.results, outcome.scores):
            if item.status is not EvidenceStatus.CURRENT:
                # Only verified CURRENT sources may enter the model context;
                # stale/deleted hits only trigger reconciliation.
                continue
            evidence.append(_evidence(item, self._runtime))
        return bounded_context(self._require_runtime(), evidence[:limit])

    # -- EventSource --------------------------------------------------------

    def event_sources(self) -> tuple[EventSourceDescriptor, ...]:
        from personal_assistant_sdk import EventSourceDescriptor

        return (
            EventSourceDescriptor(
                id=EVENT_SOURCE_ID,
                event_types=(
                    "knowledge.file_added",
                    "knowledge.file_modified",
                    "knowledge.file_moved",
                    "knowledge.file_deleted",
                ),
                description="Authorized root file changes detected by polling scans.",
            ),
        )

    async def poll(self, request: PollRequest) -> PollResult:
        if request.source_id != EVENT_SOURCE_ID:
            raise ValueError(f"unknown event source: {request.source_id}")
        cursor = _parse_cursor(request.cursor)
        limit = request.limit if 1 <= request.limit <= MAX_POLL_LIMIT else DEFAULT_POLL_LIMIT
        index = self._require_index()
        await index.detect_changes()
        rows = await index.store.fetch_events(after=cursor, limit=limit)
        events: list[DomainEvent] = []
        next_cursor = cursor
        for row in rows:
            sequence = int(row["sequence"])
            next_cursor = max(next_cursor, sequence)
            events.append(
                DomainEvent(
                    type=str(row["event_type"]),
                    source_dedupe_key=str(row["dedupe_key"]),
                    occurred_at=_isoformat(row.get("occurred_at")),
                    payload={
                        "source_id": str(row["source_id"]),
                        "root_key": str(row["root_key"]),
                        "relative_path": str(row["relative_path"]),
                        "content_hash": row.get("content_hash"),
                        "previous_hash": row.get("previous_hash"),
                        "sequence": sequence,
                    },
                )
            )
        return PollResult(
            events=tuple(events),
            source_dedupe_keys=tuple(event.source_dedupe_key for event in events),
            next_cursor=str(next_cursor),
        )

    # -- ScheduleProvider ---------------------------------------------------

    def schedules(self) -> tuple[ScheduleDefinition, ...]:
        return (
            ScheduleDefinition(
                id="knowledge.reconcile",
                timezone="Asia/Shanghai",
                misfire_policy=ScheduleMisfirePolicy.COALESCE,
                interval_seconds=900,
            ),
        )

    # -- MigrationProvider --------------------------------------------------

    def migrations(self) -> tuple[MigrationDescriptor, ...]:
        return tuple(
            MigrationDescriptor(
                version=int(item["version"]),
                checksum=str(item["checksum"]),
                description=str(item["description"]),
                path=str(item["path"]),
            )
            for item in MIGRATIONS
        )

    # -- FormSchemaProvider -------------------------------------------------

    def forms(self) -> tuple[FormSchemaDescriptor, ...]:
        return (
            FormSchemaDescriptor(
                id="knowledge.roots",
                json_schema=_load_json("schemas/config.json"),
                ui_schema={
                    "roots": {"ui:widget": "array"},
                    "embedding": {"ui:collapsed": True},
                },
                field_sensitivity={
                    "roots": SENSITIVITY,
                    "include_hidden": "PUBLIC",
                    "max_file_bytes": "PUBLIC",
                    "embedding": "PUBLIC",
                },
            ),
        )

    # -- internals ----------------------------------------------------------

    def _require_runtime(self) -> RuntimeContext:
        if self._runtime is None:
            raise ValueError("extension is not initialized")
        return self._runtime

    def _authorized_roots(self) -> tuple[AuthorizedRoot, ...]:
        runtime = self._require_runtime()
        raw = workspace_config(runtime, include_modes=False).get("roots", [])
        specs = parse_roots(raw)
        return resolve_roots(specs)

    async def _workspace_overview(self, *, root_keys: Sequence[str] | None) -> Evidence:
        runtime = self._require_runtime()
        snapshot = runtime_workspace_snapshot(runtime, root_keys=root_keys)
        status = await self._workspace_index_status()
        records = None
        if status["index_state"] == "READY" and runtime.host_data is not None:
            files = [(item["root_key"], item["relative_path"]) for item in snapshot["files"]]
            try:
                records = await KnowledgeStore(runtime.host_data).workspace_index_records(files)
            except Exception:
                # Absence of usable metadata must not be reported as absence of an index.
                status = {"index_state": "UNAVAILABLE",
                          "index_reason": "FILE_INDEX_METADATA_UNAVAILABLE"}
            else:
                # A different root binding or a new incomplete scan invalidates this projection.
                status = await self._workspace_index_status()
                if status["index_state"] != "READY":
                    records = None
        return workspace_evidence(runtime, snapshot=snapshot, index_status=status,
                                  index_records=records)

    async def _workspace_index_status(self) -> dict[str, str]:
        runtime = self._require_runtime()
        if runtime.host_data is None:
            return {"index_state": "UNAVAILABLE", "index_reason": "INDEX_METADATA_UNAVAILABLE"}
        try:
            config = workspace_config(runtime, include_modes=False)
            max_file_bytes, include_hidden = _scan_options(config)
            fingerprint = binding_fingerprint(
                self._authorized_roots(), max_file_bytes=max_file_bytes,
                include_hidden=include_hidden,
                workspace_revision=config.get("_workspace_revision"),
                workspace_id=config.get("_workspace_id"),
                embedding_config=config.get("embedding"),
            )
        except (ValueError, OSError):
            return {"index_state": "UNAVAILABLE", "index_reason": "ROOT_CONFIGURATION_UNAVAILABLE"}
        store = KnowledgeStore(runtime.host_data)
        status = await read_index_status(store, fingerprint)
        # Durable success wins over this replica's earlier background failure.
        if status["index_state"] == "READY":
            return status
        try:
            lease = await store.get_build_lease()
        except Exception:
            return status
        if lease and lease.get("binding_hash") == fingerprint:
            if lease.get("state") == "FAILED":
                return {"index_state": "FAILED", "index_reason": "INDEX_BUILD_FAILED"}
            if lease.get("state") == "RUNNING" and lease.get("active") is True:
                return {"index_state": "INDEXING", "index_reason": "INDEX_BUILD_RUNNING"}
        if self._indexing_task is not None and not self._indexing_task.done():
            return {"index_state": "INDEXING", "index_reason": "INDEX_BUILD_WAITING"}
        if self._indexing_error:
            return {"index_state": "FAILED", "index_reason": self._indexing_error}
        if (lease and lease.get("binding_hash") == fingerprint
                and lease.get("state") == "RUNNING"):
            return {"index_state": "PENDING", "index_reason": "INDEX_LEASE_EXPIRED"}
        return status

    def _require_index(self) -> KnowledgeIndex:
        if self._index is not None:
            return self._index
        runtime = self._require_runtime()
        if runtime.host_data is None:
            raise ValueError("DATA_UNAVAILABLE: the host data capability is not available")
        roots = self._authorized_roots()
        config = workspace_config(runtime, include_modes=False)
        embedder: EmbeddingProvider = build_embedding_provider(config.get("embedding"))
        max_file_bytes, include_hidden = _scan_options(config)
        self._index = KnowledgeIndex(
            KnowledgeStore(runtime.host_data),
            roots,
            embedder,
            migrations=MIGRATIONS,
            max_file_bytes=max_file_bytes,
            include_hidden=bool(include_hidden),
            extension_id=runtime.extension_id,
            extension_version=runtime.extension_version,
            workspace_revision=config.get("_workspace_revision"),
            workspace_id=config.get("_workspace_id"),
            embedding_config=config.get("embedding"),
        )
        return self._index


def _scan_options(config: Mapping[str, Any]) -> tuple[int, bool]:
    max_file_bytes = config.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)
    if (isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int)
            or not 1024 <= max_file_bytes <= 100 * 1024 * 1024):
        raise ValueError("max_file_bytes is out of range")
    include_hidden = config.get("include_hidden", False)
    if not isinstance(include_hidden, bool):
        raise ValueError("include_hidden must be a boolean")
    return max_file_bytes, include_hidden


def _search_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise ValueError("limit must be between 1 and 50")
    return int(value)


def _filters(value: Any) -> dict[str, list[str]] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("filters must be an object")
    normalized: dict[str, list[str]] = {}
    for key, items in value.items():
        if key not in {"root_keys", "media_types", "source_ids"}:
            raise ValueError(f"unknown filter: {key}")
        if not isinstance(items, (list, tuple)):
            raise ValueError(f"filter {key} must be an array")
        values: list[str] = []
        for item in items:
            if not isinstance(item, str) or not item or len(item) > 128:
                raise ValueError(f"filter {key} contains an invalid value")
            values.append(item)
        if values:
            normalized[str(key)] = values
    return normalized


def _parse_cursor(value: str | None) -> int:
    if value is None or value == "":
        return 0
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("cursor must be a non-negative integer")
    try:
        cursor: int = int(value)
    except ValueError as exc:
        raise ValueError("cursor must be a non-negative integer") from exc
    if cursor < 0:
        raise ValueError("cursor must be a non-negative integer")
    return cursor


def _isoformat(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return str(value.isoformat())
    if isinstance(value, str) and value:
        return value
    return datetime.now(UTC).isoformat()


def _source_uri(record: ChunkRecord) -> str:
    return f"knowledge://{quote(record.root_key, safe='')}/{quote(record.relative_path, safe='/')}"


def _locator_view(record: ChunkRecord) -> dict[str, Any]:
    locator = record.locator
    view: dict[str, Any] = {
        "kind": locator.kind,
        "start": locator.start,
        "end": locator.end,
        "label": locator.label,
    }
    if locator.page is not None:
        view["page"] = locator.page
    return view


def _result_view(item: SearchResult, runtime: RuntimeContext | None) -> dict[str, Any]:
    record = item.record
    view: dict[str, Any] = {
        "text": item.text,
        "source_uri": _source_uri(record),
        "source_id": record.source_id,
        "source_version": record.version_id,
        "content_hash": record.content_hash,
        "locator": _locator_view(record),
        "heading_path": list(record.heading_path),
        "media_type": record.media_type,
        "extension_id": runtime.extension_id if runtime else "personal.knowledge",
        "extension_version": runtime.extension_version if runtime else EXTENSION_VERSION,
        "sensitivity": SENSITIVITY,
        "trust": TRUST,
        "status": item.status.value,
        "score": round(float(item.score), 9),
    }

    if record.locator.page is not None:
        view["page"] = record.locator.page
    if record.locator.kind == "line_range":
        view["line_start"] = record.locator.start
        view["line_end"] = record.locator.end
    return view


def _evidence(item: SearchResult, runtime: RuntimeContext | None) -> Evidence:
    record = item.record
    status = item.status
    metadata: dict[str, Any] = {
        "status": status.value,
        "source_uri": _source_uri(record),
        "content_hash": record.source_hash,
        "source_version": record.version_id,
        "source_content_hash": record.source_hash,
        "locator": _locator_view(record),
        "heading_path": list(record.heading_path),
        "media_type": record.media_type,
        "extension_id": runtime.extension_id if runtime else "personal.knowledge",
        "extension_version": runtime.extension_version if runtime else EXTENSION_VERSION,
        "sensitivity": SENSITIVITY,
        "trust": TRUST,
        "observed_at": datetime.now(UTC).isoformat(),
    }
    if record.locator.kind == "line_range":
        metadata["line_start"] = record.locator.start
        metadata["line_end"] = record.locator.end
    if record.locator.page is not None:
        metadata["page"] = record.locator.page
    return Evidence(
        text=record.text,
        source_id=f"knowledge:{record.source_id}",
        content_hash=record.source_hash,
        source_uri=_source_uri(record),
        metadata=metadata,
    )


def _report_view(report: ReconcileReport) -> dict[str, Any]:
    return report.as_json()


def create_extension() -> PersonalKnowledgeExtension:
    return PersonalKnowledgeExtension()


__all__ = [
    "DeterministicTestEmbeddingProvider",
    "PersonalKnowledgeExtension",
    "create_extension",
    "load_migrations",
]


if __name__ == "__main__":
    run_stdio_worker(create_extension)
