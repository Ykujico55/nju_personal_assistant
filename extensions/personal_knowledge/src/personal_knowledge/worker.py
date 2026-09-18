"""Personal knowledge extension worker.

Registers the standard slots for authorized-root knowledge retrieval.  The
extension talks to PostgreSQL only through the host's generic data capability:
no credential, connection string or host path crosses the worker boundary.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
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
)
from personal_assistant_sdk.worker import run_stdio_worker

from .embedding import (
    DeterministicTestEmbeddingProvider,
    EmbeddingProvider,
    EmbeddingUnavailable,
    build_embedding_provider,
)
from .index import KnowledgeIndex
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

DEFAULT_MAX_FILE_BYTES = 16 * 1024 * 1024
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

    # -- Extension protocol -------------------------------------------------

    async def initialize(self, runtime: RuntimeContext) -> ExtensionInfo:
        self._runtime = runtime
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
        except RootConfigurationError as exc:
            if exc.code in {"roots_invalid", "roots_empty", "roots_too_many"}:
                return HealthReport(healthy=True, status="awaiting_configuration")
            return HealthReport(
                healthy=False, status="config_invalid", details={"code": exc.code}
            )
        try:
            config = self._runtime.non_secret_config
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
        return HealthReport(healthy=True, status="ready")

    async def drain(self, deadline: float) -> DrainReport:
        del deadline
        self._draining = True
        return DrainReport(drained=True, active_calls=0)

    async def shutdown(self) -> None:
        self._draining = True
        if self._index is not None:
            await self._index.embedder.aclose()
        self._index = None
        self._runtime = None

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
        report = await index.reconcile(mode=str(mode))
        return ToolResult(outcome=Outcome.SUCCEEDED, output=_report_view(report))

    # -- ContextProvider ----------------------------------------------------

    async def retrieve(self, query: ContextQuery) -> tuple[Evidence, ...]:
        if not query.text.strip() or query.limit <= 0:
            # Install-time contract verification probes with an empty query and
            # must never touch the database or filesystem.
            return ()
        index = self._require_index()
        outcome = await index.search(
            query.text,
            limit=_search_limit(query.limit),
            filters=_filters(dict(query.filters) if query.filters else None),
        )
        evidence: list[Evidence] = []
        for item in await index.evaluate(outcome.results, outcome.scores):
            if item.status is not EvidenceStatus.CURRENT:
                # Only verified CURRENT sources may enter the model context;
                # stale/deleted hits only trigger reconciliation.
                continue
            evidence.append(_evidence(item, self._runtime))
        return tuple(evidence)

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
        raw = runtime.non_secret_config.get("roots", [])
        specs = parse_roots(raw)
        return resolve_roots(specs)

    def _require_index(self) -> KnowledgeIndex:
        if self._index is not None:
            return self._index
        runtime = self._require_runtime()
        if runtime.host_data is None:
            raise ValueError("DATA_UNAVAILABLE: the host data capability is not available")
        roots = self._authorized_roots()
        config = runtime.non_secret_config
        embedder: EmbeddingProvider = build_embedding_provider(config.get("embedding"))
        max_file_bytes = config.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)
        if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int):
            raise ValueError("max_file_bytes must be an integer")
        if not 1024 <= max_file_bytes <= 100 * 1024 * 1024:
            raise ValueError("max_file_bytes is out of range")
        include_hidden = config.get("include_hidden", False)
        self._index = KnowledgeIndex(
            KnowledgeStore(runtime.host_data),
            roots,
            embedder,
            migrations=MIGRATIONS,
            max_file_bytes=max_file_bytes,
            include_hidden=bool(include_hidden),
            extension_id=runtime.extension_id,
            extension_version=runtime.extension_version,
        )
        return self._index


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
    return {
        "text": item.text,
        "source_uri": _source_uri(record),
        "source_id": record.source_id,
        "source_version": record.version_id,
        "content_hash": record.content_hash,
        "locator": _locator_view(record),
        "heading_path": list(record.heading_path),
        "page": record.locator.page,
        "line_start": record.locator.start if record.locator.kind == "line_range" else None,
        "line_end": record.locator.end if record.locator.kind == "line_range" else None,
        "media_type": record.media_type,
        "extension_id": runtime.extension_id if runtime else "personal.knowledge",
        "extension_version": runtime.extension_version if runtime else "0.1.0",
        "sensitivity": SENSITIVITY,
        "trust": TRUST,
        "status": item.status.value,
        "score": round(float(item.score), 9),
    }


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
        "extension_version": runtime.extension_version if runtime else "0.1.0",
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
