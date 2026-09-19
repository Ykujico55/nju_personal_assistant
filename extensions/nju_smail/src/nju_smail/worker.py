"""nju.smail extension worker.

Reads mail through the host mail broker (read-only IMAP), stores versioned
drafts and materializes approved send bytes through the host artifact store.
The worker never opens SMTP, never holds a client password and never treats
message content as an instruction.
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
    ArtifactHandle,
    ContextQuery,
    DomainEvent,
    DrainReport,
    EventSourceDescriptor,
    Evidence,
    ExtensionInfo,
    FormSchemaDescriptor,
    HealthReport,
    HostCapabilityError,
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

from .drafts import DraftService
from .models import MailConfigError, parse_settings
from .send import SendService
from .store import MailStore
from .sync import EVENT_TYPE, MailSynchronizer

EVENT_SOURCE_ID = "smail.poll_inbox"
CONTEXT_PROVIDER_ID = "smail.thread_history"
WORKFLOW_ID = "smail.reply_flow"
SCHEDULE_ID = "smail.poll_every_5m"
FORM_ID = "smail.account_settings"
MIGRATION_ID = "smail.mail_schema"
SENSITIVITY = "PERSONAL"
TRUST = "EXTERNAL_MESSAGE"


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
        version = int(path.name.split("_", 1)[0])
        descriptors.append(
            {
                "version": version,
                "path": f"migrations/{path.name}",
                "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
                "description": f"smail mail schema {path.stem}",
            }
        )
    return tuple(descriptors)


MIGRATIONS = load_migrations()


class SmailExtension:
    def __init__(self) -> None:
        self._runtime: RuntimeContext | None = None
        self._settings: Any = None
        self._store: MailStore | None = None
        self._sync: MailSynchronizer | None = None
        self._drafts: DraftService | None = None
        self._send: SendService | None = None
        self._draining = False
        self._schema_ready = False

    # -- Extension protocol -------------------------------------------------

    async def initialize(self, runtime: RuntimeContext) -> ExtensionInfo:
        self._runtime = runtime
        self._settings = parse_settings(dict(runtime.non_secret_config))
        return ExtensionInfo(
            id=runtime.extension_id,
            version=runtime.extension_version,
            protocol_version=PROTOCOL_VERSION,
            slots=(
                "ToolProvider",
                "ContextProvider",
                "EventSource",
                "WorkflowProvider",
                "ScheduleProvider",
                "FormSchemaProvider",
                "MigrationProvider",
            ),
            schema_hash=runtime.manifest_schema_hash,
        )

    async def health(self) -> HealthReport:
        if self._runtime is None or self._draining:
            return HealthReport(healthy=False, status="draining")
        if not self._settings.configured:
            return HealthReport(healthy=True, status="awaiting_configuration")
        if self._runtime.host_data is None:
            return HealthReport(
                healthy=False,
                status="data_unavailable",
                details={"code": "DATA_UNAVAILABLE"},
            )
        return HealthReport(healthy=True, status="ready")

    async def drain(self, deadline: float) -> DrainReport:
        del deadline
        self._draining = True
        return DrainReport(drained=True, active_calls=0)

    async def shutdown(self) -> None:
        self._draining = True
        self._store = None
        self._sync = None
        self._drafts = None
        self._send = None
        self._runtime = None

    # -- ToolProvider -------------------------------------------------------

    def tools(self) -> tuple[ToolDescriptor, ...]:
        return (
            ToolDescriptor(
                id="smail.search",
                risk=RiskLevel.READ,
                description="Search synchronized mail metadata and provenance.",
                input_schema=_load_json("schemas/search-input.json"),
                output_schema=_load_json("schemas/search-output.json"),
            ),
            ToolDescriptor(
                id="smail.prepare_reply",
                risk=RiskLevel.INTERNAL_WRITE,
                description="Create or edit a versioned reply draft artifact.",
                input_schema=_load_json("schemas/reply-input.json"),
                output_schema=_load_json("schemas/draft-output.json"),
            ),
            ToolDescriptor(
                id="smail.sync",
                risk=RiskLevel.INTERNAL_WRITE,
                description="Run a bounded read-only synchronization.",
                input_schema=_load_json("schemas/sync-input.json"),
                output_schema=_load_json("schemas/sync-output.json"),
            ),
            ToolDescriptor(
                id="smail.send",
                risk=RiskLevel.EXTERNAL_WRITE,
                description=(
                    "Materialize the approved draft bytes for the host gateway; "
                    "the extension never submits SMTP itself."
                ),
                input_schema=_load_json("schemas/send-input.json"),
                output_schema=_load_json("schemas/send-output.json"),
            ),
            ToolDescriptor(
                id="smail.send_status",
                risk=RiskLevel.INTERNAL_WRITE,
                description=(
                    "Project the host-authoritative send status into the local "
                    "draft/action projection."
                ),
                input_schema=_load_json("schemas/send-status-input.json"),
                output_schema=_load_json("schemas/send-status-output.json"),
            ),
            ToolDescriptor(
                id="smail.reconcile_send",
                risk=RiskLevel.INTERNAL_WRITE,
                description="Reconcile an UNKNOWN send against the Sent folder.",
                input_schema=_load_json("schemas/reconcile-input.json"),
                output_schema=_load_json("schemas/reconcile-output.json"),
            ),
        )

    async def invoke(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        context: InvocationContext,
    ) -> ToolResult:
        request_id = context.idempotency_key or None
        if tool_id == "smail.search":
            return await self._invoke_search(arguments)
        if tool_id == "smail.prepare_reply":
            drafts = await self._require_drafts()
            return ToolResult(
                outcome=Outcome.SUCCEEDED,
                output=await drafts.prepare(arguments, request_id),
            )
        if tool_id == "smail.sync":
            return await self._invoke_sync(arguments)
        if tool_id == "smail.send":
            send = await self._require_send()
            output = await send.materialize(arguments)
            return ToolResult(
                outcome=Outcome.SUCCEEDED,
                output=output,
                artifact_handles=(
                    ArtifactHandle(
                        id=str(output["mime_artifact_id"]),
                        content_hash=str(output["mime_sha256"]),
                        media_type="message/rfc822",
                        size_bytes=0,
                    ),
                ),
            )
        if tool_id == "smail.send_status":
            send = await self._require_send()
            return ToolResult(
                outcome=Outcome.SUCCEEDED, output=await send.sync_status(arguments)
            )
        if tool_id == "smail.reconcile_send":
            send = await self._require_send()
            return ToolResult(
                outcome=Outcome.SUCCEEDED, output=await send.reconcile(arguments)
            )
        raise MailConfigError("SMAL_TOOL_UNKNOWN", f"unknown tool: {tool_id}")

    async def _invoke_search(self, arguments: Mapping[str, Any]) -> ToolResult:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise MailConfigError("SMAL_SEARCH_INVALID", "query must be a non-empty string")
        limit = arguments.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise MailConfigError("SMAL_SEARCH_INVALID", "limit must be between 1 and 50")
        account_id = arguments.get("account_id")
        if account_id is not None and not isinstance(account_id, str):
            raise MailConfigError("SMAL_SEARCH_INVALID", "account_id must be a string")
        store = await self._require_store()
        rows = await store.search(query[:512], limit=limit, account_id=account_id)
        return ToolResult(
            outcome=Outcome.SUCCEEDED,
            output={
                "unknown": not rows,
                "results": [_result_view(row, self._runtime) for row in rows],
            },
        )

    async def _invoke_sync(self, arguments: Mapping[str, Any]) -> ToolResult:
        account_id = arguments.get("account_id")
        if account_id is not None and not isinstance(account_id, str):
            raise MailConfigError("SMAL_SYNC_INVALID", "account_id must be a string")
        folder = arguments.get("folder")
        if folder is not None and (not isinstance(folder, str) or not folder):
            raise MailConfigError("SMAL_SYNC_INVALID", "folder must be a non-empty string")
        force = bool(arguments.get("force", False))
        synchronizer = await self._require_sync()
        report = await synchronizer.sync(
            account_id=account_id, folder=folder, force=force
        )
        output = report.as_json()
        needs_action = any(
            item["status"] == "NEEDS_USER_ACTION" for item in output["accounts"]
        )
        return ToolResult(
            outcome=Outcome.NEEDS_USER_ACTION if needs_action else Outcome.SUCCEEDED,
            output=output,
        )

    # -- ContextProvider ----------------------------------------------------

    async def retrieve(self, query: ContextQuery) -> tuple[Evidence, ...]:
        if not query.text.strip() or query.limit <= 0 or not self._settings.configured:
            return ()
        store = await self._require_store()
        filters = dict(query.filters) if query.filters else {}
        thread_id = filters.get("thread_id")
        account_id = filters.get("account_id")
        limit = max(1, min(int(query.limit), 50))
        if isinstance(thread_id, str) and isinstance(account_id, str):
            rows = await store.thread_messages(account_id, thread_id, limit=limit)
        else:
            rows = await store.search(
                query.text[:512],
                limit=limit,
                account_id=account_id if isinstance(account_id, str) else None,
            )
        return tuple(_evidence(row, self._runtime) for row in rows)

    # -- EventSource --------------------------------------------------------

    def event_sources(self) -> tuple[EventSourceDescriptor, ...]:
        return (
            EventSourceDescriptor(
                id=EVENT_SOURCE_ID,
                event_types=(EVENT_TYPE,),
                description="New mail detected by read-only IMAP polling.",
            ),
        )

    async def poll(self, request: PollRequest) -> PollResult:
        if request.source_id != EVENT_SOURCE_ID:
            raise MailConfigError("SMAL_EVENT_SOURCE_UNKNOWN", "unknown event source")
        cursor = _parse_cursor(request.cursor)
        if not self._settings.configured:
            return PollResult(events=(), source_dedupe_keys=(), next_cursor=str(cursor))
        limit = request.limit if 1 <= request.limit <= 500 else 100
        try:
            synchronizer = await self._require_sync()
            await synchronizer.sync()
        except (HostCapabilityError, MailConfigError):
            # A degraded sync never fabricates events; the persisted sync state
            # carries NEEDS_USER_ACTION/BACKOFF for surfaced user attention.
            return PollResult(events=(), source_dedupe_keys=(), next_cursor=str(cursor))
        store = await self._require_store()
        rows = await store.events_after(cursor, limit=limit)
        events: list[DomainEvent] = []
        next_cursor = cursor
        for row in rows:
            sequence = int(row["sequence"])
            next_cursor = max(next_cursor, sequence)
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    payload = {}
            events.append(
                DomainEvent(
                    type=str(row["event_type"]),
                    source_dedupe_key=str(row["dedupe_key"]),
                    occurred_at=_isoformat(row.get("created_at")),
                    payload=payload if isinstance(payload, Mapping) else {},
                )
            )
        return PollResult(
            events=tuple(events),
            source_dedupe_keys=tuple(event.source_dedupe_key for event in events),
            next_cursor=str(next_cursor),
        )

    # -- WorkflowProvider ---------------------------------------------------

    def workflows(self) -> tuple[Any, ...]:
        from personal_assistant_sdk import WorkflowDefinition

        return (
            WorkflowDefinition(
                id=WORKFLOW_ID,
                version="1",
                steps=(
                    {
                        "id": "search",
                        "capability": "smail.search",
                        "risk": "READ",
                    },
                    {
                        "id": "prepare",
                        "capability": "smail.prepare_reply",
                        "risk": "INTERNAL_WRITE",
                    },
                    {
                        "id": "approve",
                        "kind": "approval",
                        "risk": "EXTERNAL_WRITE",
                        "requires_exact_snapshot": True,
                    },
                    {
                        "id": "send",
                        "capability": "smail.send",
                        "risk": "EXTERNAL_WRITE",
                        "requires_approval": True,
                    },
                    {
                        "id": "reconcile",
                        "capability": "smail.reconcile_send",
                        "risk": "INTERNAL_WRITE",
                        "only_after_unknown": True,
                    },
                ),
            ),
        )

    # -- ScheduleProvider ---------------------------------------------------

    def schedules(self) -> tuple[ScheduleDefinition, ...]:
        interval = 300
        if self._settings is not None and self._settings.configured:
            interval = int(self._settings.poll_interval_seconds)
        return (
            ScheduleDefinition(
                id=SCHEDULE_ID,
                timezone="Asia/Shanghai",
                misfire_policy=ScheduleMisfirePolicy.COALESCE,
                interval_seconds=interval,
            ),
        )

    # -- FormSchemaProvider -------------------------------------------------

    def forms(self) -> tuple[FormSchemaDescriptor, ...]:
        return (
            FormSchemaDescriptor(
                id=FORM_ID,
                json_schema=_load_json("schemas/config.json"),
                ui_schema={
                    "accounts": {"ui:widget": "array"},
                    "folders": {"ui:collapsed": True},
                    "poll_interval_seconds": {"ui:collapsed": True},
                },
                field_sensitivity={
                    "accounts": "PERSONAL",
                    "folders": "PUBLIC",
                    "poll_interval_seconds": "PUBLIC",
                    "max_messages_per_sync": "PUBLIC",
                    "max_message_bytes": "PUBLIC",
                },
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

    # -- internals ----------------------------------------------------------

    async def _require_store(self) -> MailStore:
        if self._store is not None and self._schema_ready:
            return self._store
        runtime = self._require_runtime()
        if runtime.host_data is None:
            raise MailConfigError("SMAL_DATA_UNAVAILABLE", "the data capability is unavailable")
        store = MailStore(runtime.host_data)
        await store.migrate(MIGRATIONS)
        self._schema_ready = True
        self._store = store
        return store

    async def _require_sync(self) -> MailSynchronizer:
        if self._sync is not None:
            return self._sync
        runtime = self._require_runtime()
        if runtime.host_mail is None:
            raise MailConfigError("SMAL_MAIL_UNAVAILABLE", "the mail capability is unavailable")
        store = await self._require_store()
        self._sync = MailSynchronizer(
            store, runtime.host_mail, self._settings, migrations=MIGRATIONS
        )
        return self._sync

    async def _require_drafts(self) -> DraftService:
        if self._drafts is not None:
            return self._drafts
        runtime = self._require_runtime()
        store = await self._require_store()
        self._drafts = DraftService(
            store, self._settings, runtime.host_artifact, runtime.host_mail
        )
        return self._drafts

    async def _require_send(self) -> SendService:
        if self._send is not None:
            return self._send
        runtime = self._require_runtime()
        store = await self._require_store()
        self._send = SendService(store, self._settings, runtime.host_mail)
        return self._send

    def _require_runtime(self) -> RuntimeContext:
        if self._runtime is None:
            raise MailConfigError("SMAL_NOT_INITIALIZED", "the extension is not initialized")
        return self._runtime


def _result_view(row: Mapping[str, Any], runtime: RuntimeContext | None) -> dict[str, Any]:
    return {
        "account_id": row.get("account_id"),
        "folder": row.get("folder_name"),
        "uid": row.get("uid"),
        "uidvalidity": row.get("uidvalidity"),
        "message_id": row.get("message_id"),
        "content_hash": row.get("content_hash"),
        "subject": row.get("subject"),
        "snippet": row.get("snippet"),
        "from_address": row.get("from_address"),
        "from_name": row.get("from_name"),
        "sent_at": _isoformat(row.get("sent_at")),
        "thread_id": row.get("thread_id"),
        "source_uri": _source_uri(row),
        "sensitivity": SENSITIVITY,
        "trust": TRUST,
        "extension_id": runtime.extension_id if runtime else "nju.smail",
        "extension_version": runtime.extension_version if runtime else "0.1.0",
    }


def _evidence(row: Mapping[str, Any], runtime: RuntimeContext | None) -> Evidence:
    return Evidence(
        text=str(row.get("snippet") or row.get("subject") or ""),
        source_id=f"smail:{row.get('message_pk')}",
        content_hash=str(row.get("content_hash") or ""),
        source_uri=_source_uri(row),
        metadata={
            "account_id": row.get("account_id"),
            "folder": row.get("folder_name"),
            "uid": row.get("uid"),
            "uidvalidity": row.get("uidvalidity"),
            "message_id": row.get("message_id"),
            "thread_id": row.get("thread_id"),
            "subject": row.get("subject"),
            "from_address": row.get("from_address"),
            "sensitivity": SENSITIVITY,
            "trust": TRUST,
            "extension_id": runtime.extension_id if runtime else "nju.smail",
            "extension_version": runtime.extension_version if runtime else "0.1.0",
            "status": "CURRENT",
        },
    )


def _source_uri(row: Mapping[str, Any]) -> str:
    account = quote(str(row.get("account_id") or ""), safe="")
    folder = quote(str(row.get("folder_name") or ""), safe="")
    uid = row.get("uid")
    uidvalidity = row.get("uidvalidity")
    message_id = quote(str(row.get("message_id") or ""), safe="")
    return (
        f"smail://{account}/{folder}/{uid}"
        f"?uidvalidity={uidvalidity}&message_id={message_id}"
    )


def _parse_cursor(value: str | None) -> int:
    if value is None or value == "":
        return 0
    try:
        cursor = int(value)
    except (TypeError, ValueError) as exc:
        raise MailConfigError(
            "SMAL_CURSOR_INVALID", "cursor must be a non-negative integer"
        ) from exc
    if cursor < 0:
        raise MailConfigError("SMAL_CURSOR_INVALID", "cursor must be a non-negative integer")
    return cursor


def _isoformat(value: Any) -> str:
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.isoformat()
    if isinstance(value, str) and value:
        return value
    return datetime.now(UTC).isoformat()


def create_extension() -> SmailExtension:
    return SmailExtension()


__all__ = ["MIGRATIONS", "SmailExtension", "create_extension", "load_migrations"]


if __name__ == "__main__":
    run_stdio_worker(create_extension)
