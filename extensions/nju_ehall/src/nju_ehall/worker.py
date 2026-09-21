"""Worker entrypoint for the supervised ehall extension.

Every page write is delegated to the host: ``ehall.fill_form`` and
``ehall.submit`` are executed by host executors behind the Tool Gateway and an
R2 approval.  This worker only reads bounded structured snapshots.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from personal_assistant_sdk import (
    PROTOCOL_VERSION,
    ContextQuery,
    DrainReport,
    Evidence,
    ExtensionInfo,
    FormSchemaDescriptor,
    HealthReport,
    HostBrowserClient,
    HostCapabilityError,
    InvocationContext,
    MigrationDescriptor,
    Outcome,
    RiskLevel,
    RuntimeContext,
    ToolDescriptor,
    ToolResult,
    WorkflowDefinition,
)
from personal_assistant_sdk.models import JsonValue
from personal_assistant_sdk.worker import run_stdio_worker

from .adapters import build_descriptor, load_adapter_documents
from .models import EhallError, EhallSettings, parse_settings
from .store import EhallStore

OPEN_TOOL = "ehall.open_transaction"
FILL_TOOL = "ehall.fill_form"
SUBMIT_TOOL = "ehall.submit"


def _package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_json(relative: str) -> dict[str, Any]:
    payload = json.loads((_package_root() / relative).read_text("utf-8"))
    if not isinstance(payload, dict):
        raise EhallError("EHALL_SCHEMA_INVALID", f"schema is not an object: {relative}")
    return payload


def load_migrations() -> tuple[dict[str, Any], ...]:
    directory = _package_root() / "migrations"
    descriptors: list[dict[str, Any]] = []
    for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = int(path.name.split("_", 1)[0])
        descriptors.append(
            {
                "version": version,
                "path": f"migrations/{path.name}",
                "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
                "description": f"ehall schema {path.stem}",
            }
        )
    return tuple(descriptors)


MIGRATIONS = load_migrations()


def _migration_descriptors() -> tuple[MigrationDescriptor, ...]:
    return tuple(
        MigrationDescriptor(
            version=int(item["version"]),
            checksum=str(item["checksum"]),
            description=str(item["description"]),
            path=str(item["path"]),
        )
        for item in MIGRATIONS
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EhallError("EHALL_HOST_RESPONSE_INVALID", "host returned a non-object response")
    return value


def _sequence(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return list(value)


class EhallExtension:
    def __init__(self) -> None:
        self._runtime: RuntimeContext | None = None
        self._settings: EhallSettings = EhallSettings(browser_origin="")
        self._store: EhallStore | None = None
        self._adapter_registered = False
        self._adapters: (
            tuple[tuple[dict[str, Any], dict[str, Any]], ...] | None
        ) = None
        self._draining = False

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
                "WorkflowProvider",
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
        if self._runtime.host_browser is None:
            return HealthReport(
                healthy=True,
                status="browser_unavailable",
                details={"code": "BROWSER_UNAVAILABLE"},
            )
        return HealthReport(healthy=True, status="ready")

    async def drain(self, deadline: float) -> DrainReport:
        del deadline
        self._draining = True
        return DrainReport(drained=True, active_calls=0)

    async def shutdown(self) -> None:
        self._draining = True
        self._store = None
        self._runtime = None

    # -- ToolProvider -------------------------------------------------------

    def tools(self) -> tuple[ToolDescriptor, ...]:
        return (
            ToolDescriptor(
                id="ehall.discover_apps",
                risk=RiskLevel.READ,
                description="Discover the user's visible applications in the supervised browser.",
                input_schema=_load_json("schemas/discover-input.json"),
                output_schema=_load_json("schemas/discover-output.json"),
            ),
            ToolDescriptor(
                id="ehall.inspect_transaction",
                risk=RiskLevel.READ,
                description="Inspect one confirmed low-risk transaction page (read-only).",
                input_schema=_load_json("schemas/inspect-input.json"),
                output_schema=_load_json("schemas/inspect-output.json"),
            ),
            ToolDescriptor(
                id="ehall.prepare_preview",
                risk=RiskLevel.INTERNAL_WRITE,
                description="Map materials and values into a fill plan and preview.",
                input_schema=_load_json("schemas/prepare-input.json"),
                output_schema=_load_json("schemas/prepare-output.json"),
            ),
            ToolDescriptor(
                id=OPEN_TOOL,
                risk=RiskLevel.INTERNAL_WRITE,
                description="Host-executed bound navigation into one declared transaction.",
                input_schema=_load_json("schemas/open-input.json"),
                output_schema=_load_json("schemas/open-output.json"),
            ),
            ToolDescriptor(
                id=FILL_TOOL,
                risk=RiskLevel.EXTERNAL_WRITE,
                description="Host-executed approved fill that stops before submission.",
                input_schema=_load_json("schemas/fill-input.json"),
                output_schema=_load_json("schemas/fill-output.json"),
            ),
            ToolDescriptor(
                id=SUBMIT_TOOL,
                risk=RiskLevel.EXTERNAL_WRITE,
                description="Host-executed approved final submission (default disabled).",
                input_schema=_load_json("schemas/submit-input.json"),
                output_schema=_load_json("schemas/submit-output.json"),
            ),
            ToolDescriptor(
                id="ehall.reconcile",
                risk=RiskLevel.READ,
                description="Read-only flow tracking for an UNKNOWN submission.",
                input_schema=_load_json("schemas/reconcile-input.json"),
                output_schema=_load_json("schemas/reconcile-output.json"),
            ),
        )

    async def invoke(
        self, tool_id: str, arguments: dict[str, Any], context: InvocationContext
    ) -> ToolResult:
        if self._draining:
            return _result(Outcome.RETRYABLE, "EHALL_DRAINING")
        if not self._settings.configured:
            return _result(Outcome.NEEDS_USER_ACTION, "EHALL_CONFIG_REQUIRED")
        try:
            if tool_id == "ehall.discover_apps":
                return await self._discover(arguments, context)
            if tool_id == "ehall.inspect_transaction":
                return await self._inspect(arguments)
            if tool_id == "ehall.prepare_preview":
                return await self._prepare(arguments)
            if tool_id == "ehall.reconcile":
                return await self._reconcile(arguments)
            if tool_id in {OPEN_TOOL, FILL_TOOL, SUBMIT_TOOL}:
                return _result(Outcome.PERMANENT, "EHALL_HOST_EXECUTED")
            return _result(Outcome.PERMANENT, "EHALL_UNKNOWN_TOOL")
        except EhallError as exc:
            return _result(Outcome.PERMANENT, exc.code)
        except HostCapabilityError as exc:
            if exc.code == "BROWSER_UNAVAILABLE":
                return _result(Outcome.PERMANENT, "EHALL_BROWSER_UNAVAILABLE")
            if exc.code in {"NAVIGATION_DENIED", "PROHIBITED_SEMANTICS", "UNKNOWN_TRANSACTION"}:
                return _result(Outcome.PERMANENT, f"EHALL_{exc.code}")
            return _result(Outcome.PERMANENT, "EHALL_BROWSER_ERROR")

    # -- tool implementations ----------------------------------------------

    def _adapter_pairs(self) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
        """All declared adapters, one per transaction, in stable order."""

        if self._adapters is None:
            extension_id = self._runtime.extension_id if self._runtime else "nju.ehall"
            extension_version = (
                self._runtime.extension_version if self._runtime else "0.1.0"
            )
            self._adapters = tuple(
                (
                    document,
                    build_descriptor(
                        document,
                        extension_id=extension_id,
                        extension_version=extension_version,
                        origin=self._settings.browser_origin,
                        allowed_paths=self._settings.allowed_paths,
                    ),
                )
                for document in load_adapter_documents(_package_root())
            )
        return self._adapters

    def _adapter(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._adapter_pairs()[0]

    def _adapter_for_path(self, app_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for document, descriptor in self._adapter_pairs():
            if app_path in descriptor["allowed_paths"]:
                return document, descriptor
        raise EhallError("EHALL_UNSUPPORTED_TRANSACTION", f"unknown app path {app_path!r}")

    def _adapter_for_transaction(
        self, transaction_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        for document, descriptor in self._adapter_pairs():
            if transaction_id in descriptor["transaction_ids"]:
                return document, descriptor
        raise EhallError(
            "EHALL_UNSUPPORTED_TRANSACTION", f"unknown transaction {transaction_id!r}"
        )

    def _browser(self) -> HostBrowserClient:
        if self._runtime is None or self._runtime.host_browser is None:
            raise EhallError("EHALL_BROWSER_UNAVAILABLE", "the host browser capability is absent")
        return self._runtime.host_browser

    async def _ensure_adapter(self, browser: HostBrowserClient) -> dict[str, Any]:
        document, _descriptor = self._adapter()
        if not self._adapter_registered:
            for _item, descriptor in self._adapter_pairs():
                await browser.register_adapter(descriptor)
            self._adapter_registered = True
        return document

    def _session_id(self, arguments: Mapping[str, Any]) -> str:
        value = arguments.get("session_id")
        if not isinstance(value, str) or not value:
            raise EhallError("EHALL_ARGUMENTS_INVALID", "session_id is required")
        return value

    async def _store_ready(self) -> EhallStore:
        if self._runtime is None or self._runtime.host_data is None:
            raise EhallError("EHALL_DATA_UNAVAILABLE", "the extension data capability is absent")
        if self._store is None:
            self._store = EhallStore(self._runtime.host_data)
        await self._store.migrate(MIGRATIONS)
        return self._store

    async def _discover(
        self, arguments: Mapping[str, Any], context: InvocationContext
    ) -> ToolResult:
        browser = self._browser()
        purpose = str(arguments.get("purpose", "")).strip()[:200] or "发现可用事项"
        session = _mapping(await browser.session(task_id=context.task_id, purpose=purpose))
        session_id = str(session.get("session_id", ""))
        if not session_id:
            return _result(Outcome.PERMANENT, "EHALL_SESSION_INVALID")
        document, descriptor = self._adapter()
        await self._ensure_adapter(browser)
        snapshot = _mapping(
            await browser.navigate(
                session_id,
                adapter_id=str(descriptor["adapter_id"]),
                transaction_id="discover",
                url=f"{self._settings.browser_origin}{descriptor['discovery_path']}",
            )
        )
        if not snapshot.get("authenticated", True):
            return _user_action(session_id)
        candidates: list[dict[str, str]] = []
        seen_names: set[str] = set()
        # SPA portals render service cards as buttons, not links: the declared
        # navigation actions are the authoritative list when they are visible.
        live_labels = {
            str(item.get("label", ""))
            for item in _sequence(snapshot.get("actions", ()))
            if isinstance(item, Mapping)
        }
        for _document, declared in self._adapter_pairs():
            for action in declared["actions"]:
                if action.get("kind") != "navigate":
                    continue
                name = str(action.get("label", "")).strip()[:200]
                path = str(action.get("navigates_to_path", ""))
                if not name or not path or name not in live_labels or name in seen_names:
                    continue
                seen_names.add(name)
                candidates.append({"name": name, "path": path})
        for item in _sequence(snapshot.get("actions", ())):
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("label", "")).strip()[:200]
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            candidates.append({"name": name, "path": ""})
        for item in _sequence(snapshot.get("links", ())):
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("text", "")).strip()[:200]
            path = str(item.get("path", ""))
            if (
                not name
                or not path
                or path == descriptor["discovery_path"]
                or name in seen_names
            ):
                continue
            seen_names.add(name)
            candidates.append({"name": name, "path": path})
        classified = await browser.classify_labels(
            [item["name"] for item in candidates],
            forbidden_terms=tuple(str(term) for term in descriptor.get("forbidden_terms", ())),
        )
        matches_by_label: dict[str, list[str]] = {}
        for entry in classified:
            if not isinstance(entry, Mapping):
                continue
            categories = [
                str(match.get("category", ""))
                for match in _sequence(entry.get("matched", ()))
                if isinstance(match, Mapping)
            ]
            matches_by_label[str(entry.get("label", ""))] = categories
        apps: list[dict[str, Any]] = []
        for candidate in candidates:
            categories = matches_by_label.get(candidate["name"], [])
            apps.append(
                {
                    "name": candidate["name"],
                    "path": candidate["path"],
                    "supported": any(
                        candidate["path"] in declared["allowed_paths"]
                        for _document, declared in self._adapter_pairs()
                    ),
                    "prohibited": bool(categories),
                    "risk": "PROHIBITED" if categories else "READ",
                    "categories": categories,
                }
            )
        await browser.record_discovery(session_id, app_count=len(apps))
        return ToolResult(
            Outcome.SUCCEEDED,
            _output({"session_id": session_id, "state": "DISCOVERED", "apps": apps}),
        )

    async def _inspect(self, arguments: Mapping[str, Any]) -> ToolResult:
        session_id = self._session_id(arguments)
        app_path = str(arguments.get("app_path", ""))
        try:
            document, descriptor = self._adapter_for_path(app_path)
        except EhallError:
            return _result(Outcome.PERMANENT, "EHALL_UNSUPPORTED_TRANSACTION")
        browser = self._browser()
        await self._ensure_adapter(browser)
        snapshot = _mapping(
            await browser.navigate(
                session_id,
                adapter_id=str(descriptor["adapter_id"]),
                transaction_id=str(descriptor["transaction_ids"][0]),
                url=f"{self._settings.browser_origin}{app_path}",
            )
        )
        if not snapshot.get("authenticated", True):
            return _user_action(session_id)
        if _risk_blocked(snapshot):
            return ToolResult(
                Outcome.PERMANENT,
                {
                    "session_id": session_id,
                    "error": "EHALL_PROHIBITED",
                    "categories": [
                        str(item) for item in _sequence(snapshot.get("risk_categories"))
                    ],
                },
            )
        known = [
            dict(item)
            for item in _sequence(snapshot.get("fields"))
            if isinstance(item, Mapping) and item.get("known") is True
        ]
        return ToolResult(
            Outcome.SUCCEEDED,
            _output(
                {
                    "session_id": session_id,
                    "transaction_id": str(descriptor["transaction_ids"][0]),
                    "app_id": str(document["adapter_id"]),
                    "page_fingerprint": str(snapshot.get("fingerprint", "")),
                    "risk": str(snapshot.get("risk", "READ")),
                    "origin": str(snapshot.get("origin", "")),
                    "fields": known,
                    "materials": [dict(item) for item in _sequence(document.get("materials"))],
                    "consequences": str(descriptor.get("consequences", "")),
                }
            ),
        )

    async def _prepare(self, arguments: Mapping[str, Any]) -> ToolResult:
        session_id = self._session_id(arguments)
        transaction_id = str(arguments.get("transaction_id", ""))
        values = arguments.get("values")
        if not isinstance(values, Mapping):
            return _result(Outcome.PERMANENT, "EHALL_ARGUMENTS_INVALID")
        try:
            document, descriptor = self._adapter_for_transaction(transaction_id)
        except EhallError:
            return _result(Outcome.PERMANENT, "EHALL_UNSUPPORTED_TRANSACTION")
        browser = self._browser()
        await self._ensure_adapter(browser)
        snapshot = _mapping(
            await browser.snapshot(
                session_id,
                adapter_id=str(descriptor["adapter_id"]),
                transaction_id=transaction_id,
            )
        )
        if not snapshot.get("authenticated", True):
            return _user_action(session_id)
        if _risk_blocked(snapshot):
            return _result(Outcome.PERMANENT, "EHALL_PROHIBITED")
        fields, missing = _build_fields(descriptor, snapshot, values)
        if missing:
            return ToolResult(
                Outcome.NEEDS_USER_ACTION,
                _output(
                    {
                        "session_id": session_id,
                        "state": "NEEDS_MATERIALS",
                        "missing": missing,
                    }
                ),
            )
        fingerprint = str(snapshot.get("fingerprint", ""))
        await browser.record_preparation(
            session_id,
            adapter_id=str(descriptor["adapter_id"]),
            adapter_version=str(descriptor["adapter_version"]),
            app_id=str(document["adapter_id"]),
            transaction_id=transaction_id,
            page_fingerprint=fingerprint,
            planned_fields=len(fields),
        )
        store = await self._store_ready()
        await store.upsert_transaction(
            transaction_id=transaction_id,
            adapter_id=str(descriptor["adapter_id"]),
            adapter_version=str(descriptor["adapter_version"]),
            app_id=str(document["adapter_id"]),
            page_fingerprint=fingerprint,
            status="PREPARING",
        )
        plan = {
            "session_id": session_id,
            "adapter_id": descriptor["adapter_id"],
            "adapter_version": descriptor["adapter_version"],
            "transaction_id": transaction_id,
            "app_id": document["adapter_id"],
            "expected_origin": str(snapshot.get("origin", self._settings.browser_origin)),
            "expected_page_fingerprint": fingerprint,
            "consequences": str(descriptor.get("consequences", "")),
            "fields": fields,
            "attachments": [],
        }
        preview = {
            "session_id": session_id,
            "transaction_id": transaction_id,
            "app_id": document["adapter_id"],
            "origin": str(snapshot.get("origin", "")),
            "page_fingerprint": fingerprint,
            "risk": str(snapshot.get("risk", "READ")),
            "consequences": str(descriptor.get("consequences", "")),
            "fields": fields,
            "missing_fields": [],
            "attachments": [],
            "authoritative": False,
            "note": "the authoritative preview is produced by ehall.fill_form",
        }
        return ToolResult(
            Outcome.SUCCEEDED,
            _output(
                {"session_id": session_id, "plan": plan, "preview": preview, "missing": []}
            ),
        )

    async def _reconcile(self, arguments: Mapping[str, Any]) -> ToolResult:
        session_id = self._session_id(arguments)
        browser = self._browser()
        await self._ensure_adapter(browser)
        # The host performs the read-only tracking query and issues the proof;
        # the extension only reports the host-adjudicated result.
        record = _mapping(await browser.reconcile(session_id))
        state = str(record.get("state", ""))
        receipt = record.get("receipt")
        reference = ""
        if isinstance(receipt, Mapping):
            reference = str(receipt.get("reference", ""))
        if state == "SUCCEEDED":
            return ToolResult(
                Outcome.SUCCEEDED,
                {"session_id": session_id, "result": "MATCHED", "reference": reference},
            )
        diagnostic = str(record.get("diagnostic_code", ""))
        result = "AMBIGUOUS" if diagnostic.endswith("AMBIGUOUS") else "NOT_FOUND"
        return ToolResult(
            Outcome.SUCCEEDED,
            {"session_id": session_id, "result": result, "reference": ""},
        )

    # -- ContextProvider ----------------------------------------------------

    async def retrieve(self, query: ContextQuery) -> tuple[Evidence, ...]:
        del query
        if (
            self._runtime is None
            or self._runtime.host_data is None
            or not self._settings.configured
        ):
            return ()
        try:
            store = await self._store_ready()
            rows = await store.recent_transactions(limit=10)
        except (EhallError, HostCapabilityError):
            return ()
        evidence: list[Evidence] = []
        for row in rows:
            text = (
                f"事务 {row.get('transaction_id')} 状态 {row.get('status')} "
                f"模板 {row.get('adapter_id')}@{row.get('adapter_version')}"
            )
            evidence.append(
                Evidence(
                    text=text,
                    source_id=f"ehall:{row.get('transaction_id')}",
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    source_uri=f"ehall://transaction/{row.get('transaction_id')}",
                    metadata={
                        "status": str(row.get("status", "")),
                        "app_id": str(row.get("app_id", "")),
                        "page_fingerprint": str(row.get("page_fingerprint", "")),
                    },
                )
            )
        return tuple(evidence)

    # -- WorkflowProvider / FormSchemaProvider / MigrationProvider -----------

    def workflows(self) -> tuple[WorkflowDefinition, ...]:
        return (
            WorkflowDefinition(
                id="ehall.supervised_flow",
                version="1.0.0",
                steps=(
                    {"tool": "ehall.discover_apps", "risk": "READ"},
                    {"tool": "ehall.inspect_transaction", "risk": "READ"},
                    {"tool": "ehall.prepare_preview", "risk": "INTERNAL_WRITE"},
                    {"tool": "ehall.fill_form", "risk": "EXTERNAL_WRITE"},
                    {"tool": "ehall.submit", "risk": "EXTERNAL_WRITE", "optional": True},
                    {"tool": "ehall.reconcile", "risk": "READ"},
                ),
            ),
        )

    def forms(self) -> tuple[FormSchemaDescriptor, ...]:
        document, _descriptor = self._adapter()
        properties: dict[str, Any] = {}
        required: list[str] = []
        for spec in _sequence(document.get("fields")):
            if not isinstance(spec, Mapping):
                continue
            field_id = str(spec["field_id"])
            properties[field_id] = {
                "type": "string",
                "title": str(spec.get("label", field_id)),
            }
            if spec.get("required"):
                required.append(field_id)
        json_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        return (
            FormSchemaDescriptor(
                id="ehall.transaction_form",
                json_schema=json_schema,
                ui_schema={"sensitivity": "PERSONAL"},
            ),
        )

    def migrations(self) -> tuple[MigrationDescriptor, ...]:
        return _migration_descriptors()


def _risk_blocked(snapshot: Mapping[str, Any]) -> bool:
    if str(snapshot.get("risk", "")) == "PROHIBITED":
        return True
    return bool(_sequence(snapshot.get("risk_categories")))


def _build_fields(
    descriptor: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    values: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    live: dict[str, Mapping[str, Any]] = {}
    for item in _sequence(snapshot.get("fields")):
        if isinstance(item, Mapping):
            live[str(item.get("field_id", ""))] = item
    fields: list[dict[str, Any]] = []
    missing: list[str] = []
    for spec in _sequence(descriptor.get("fields")):
        if not isinstance(spec, Mapping):
            continue
        field_id = str(spec["field_id"])
        current = live.get(field_id, {})
        old_value = str(current.get("value", ""))
        raw = values.get(field_id, "")
        new_value = raw.strip() if isinstance(raw, str) else str(raw).strip()
        if spec.get("required") and not new_value:
            missing.append(field_id)
        fields.append(
            {
                "field_id": field_id,
                "locator": str(spec.get("locator", "")),
                "label": str(spec.get("label", field_id)),
                "old_value": old_value,
                "new_value": new_value,
                "source": str(spec.get("source", "USER_INPUT")),
                "confidence": 1.0,
                "validation": "VALID" if new_value else "MISSING",
                "evidence_sha256": None,
            }
        )
    return fields, missing


def _user_action(session_id: str) -> ToolResult:
    return ToolResult(
        Outcome.NEEDS_USER_ACTION,
        _output({
            "session_id": session_id,
            "state": "WAITING_USER",
            "instruction": (
                "请在可见浏览器中亲自完成统一认证、扫码、验证码或动态验证；"
                "系统不会自动输入密码或验证码。"
            ),
        }),
    )


def _output(payload: dict[str, Any]) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", payload)


def _result(outcome: Outcome, code: str) -> ToolResult:
    return ToolResult(outcome, {"error": code, "code": code})


def create_extension() -> EhallExtension:
    return EhallExtension()


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    run_stdio_worker(create_extension)
