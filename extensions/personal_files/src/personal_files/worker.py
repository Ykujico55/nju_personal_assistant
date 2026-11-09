"""SDK wiring for explicitly configured instance roots; no host business branches."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote, unquote, urlsplit

from personal_assistant_sdk import (
    PROTOCOL_VERSION,
    ContextQuery,
    DrainReport,
    Evidence,
    ExtensionInfo,
    HealthReport,
    InvocationContext,
    Outcome,
    RiskLevel,
    RuntimeContext,
    ToolDescriptor,
    ToolResult,
    WorkspaceConfigurationError,
    workspace_config,
)
from personal_assistant_sdk.models import JsonValue
from personal_assistant_sdk.worker import run_stdio_worker

from .errors import FilesError
from .service import FileService

# Host Registry freezes schema arrays to tuples; its current branch validator
# only accepts lists. Use a flat supported schema and enforce the write/mkdir/delete
# relationship in _valid_arguments before constructing FileService.
_INPUTS: dict[str, Any] = {
    "roots": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    "list": {
        "type": "object",
        "properties": {
            "root_key": {"type": "string", "minLength": 1, "maxLength": 64},
            "path": {"type": "string", "minLength": 0, "maxLength": 1024},
            "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["root_key"],
        "additionalProperties": False,
    },
    "read": {
        "type": "object",
        "properties": {
            "root_key": {"type": "string", "minLength": 1, "maxLength": 64},
            "path": {"type": "string", "minLength": 1, "maxLength": 1024},
            "offset": {"type": "integer", "minimum": 0, "maximum": 2147483647},
            "limit": {"type": "integer", "minimum": 1, "maximum": 262144},
        },
        "required": ["root_key", "path"],
        "additionalProperties": False,
    },
    "preview": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["write", "mkdir", "delete"]},
            "root_key": {"type": "string", "minLength": 1, "maxLength": 64},
            "path": {"type": "string", "minLength": 1, "maxLength": 1024},
            "expected_sha256": {"type": ["string", "null"], "pattern": "^[a-f0-9]{64}$"},
            "content": {"type": "string", "maxLength": 262144},
        },
        "required": ["operation", "root_key", "path", "expected_sha256"],
        "additionalProperties": False,
        "description": "The SDK worker also enforces operation-specific semantics "
        "before any filesystem/state access: write requires content; "
        "mkdir requires expected_sha256=null and no content, with an "
        "existing parent and absent target; delete forbids content "
        "and requires a non-null existing file hash. Only "
        "write/mkdir plans execute via files.write. mkdir creates "
        "one directory, never parents; deletion of directories is "
        "unsupported.",
    },
    "write": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "plan_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        },
        "required": ["plan_id", "plan_hash"],
        "additionalProperties": False,
    },
    "status": {
        "type": "object",
        "properties": {"plan_id": {"type": "string", "minLength": 1, "maxLength": 128}},
        "required": ["plan_id"],
        "additionalProperties": False,
    },
}
_OUTPUT: dict[str, Any] = {
    "type": "object",
    "description": "Operation facts; a stored or replayed receipt is historical, not proof "
    "of current filesystem state. Check current files via files.read and "
    "directories via files.list; files.status also returns historical "
    "records. UNKNOWN never authorizes automatic retry.",
    "additionalProperties": True,
}
_DESCRIPTIONS = {
    "roots": "List instance-wide authorized root keys, labels and modes; no file bodies.",
    "list": "List one authorized directory, nonrecursively. Follow next_offset when present.",
    "read": "Read authorized UTF-8 text and its current SHA-256. Offsets/limits are Unicode "
    "characters; next_offset explicitly identifies unread content. No index required.",
    "preview": "Persist an immutable write/mkdir/delete plan and exact diff. For file edits, "
    "use its current SHA-256; null means absent target only. mkdir requires null hash, no content "
    "and an existing parent; create multiple levels using separate reviewed plans. Delete is "
    "only a single-file plan for separate local owner confirmation. Review the exact plan.",
    "write": "Commit the exact reviewed WRITE or MKDIR plan by plan_id/plan_hash. mkdir creates "
    "one absent directory and never creates parents or overwrites an existing entry. A conflict "
    "requires a fresh read/list and preview. A replayed receipt is historical, not current "
    "filesystem state. DELETE plans require the owner-only local CLI; no directory deletion.",
    "status": "Read a durable plan/receipt without re-executing it. Historical success does "
    "not prove current file contents. UNKNOWN must not be automatically retried.",
}
_PROVIDER_IDS = frozenset({"files.workspace", "files.document"})


def _valid_arguments(name: str, args: Mapping[str, JsonValue]) -> bool:
    spec = _INPUTS[name]
    if set(args) - spec["properties"].keys() or set(spec["required"]) - args.keys():
        return False
    for key, value in args.items():
        rule = spec["properties"][key]
        if key == "expected_sha256":
            if value is not None and (
                not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
            ):
                return False
            continue
        if rule.get("type") == "string":
            if not isinstance(value, str):
                return False
            if not rule.get("minLength", 0) <= len(value) <= rule.get("maxLength", 2**31):
                return False
            if "pattern" in rule and re.fullmatch(rule["pattern"], value) is None:
                return False
            if "enum" in rule and value not in rule["enum"]:
                return False
        elif rule.get("type") == "integer":
            if type(value) is not int or not rule["minimum"] <= value <= rule["maximum"]:
                return False
    if name == "preview":
        return (
            (args["operation"] == "write" and "content" in args)
            or (
                args["operation"] == "mkdir"
                and "content" not in args
                and args["expected_sha256"] is None
            )
            or (
                args["operation"] == "delete"
                and "content" not in args
                and args["expected_sha256"] is not None
            )
        )
    return True


def _deadline(context: InvocationContext) -> bool:
    try:
        value = datetime.fromisoformat(context.deadline.replace("Z", "+00:00"))
        return value.tzinfo is not None and value > datetime.now(UTC)
    except (ValueError, TypeError, AttributeError):
        return False


def _error(exc: Exception, *, mutation: bool) -> tuple[Outcome, dict[str, Any]]:
    if isinstance(exc, FilesError):
        code = exc.code
        if not isinstance(code, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code) is None:
            code = "FILE_OPERATION_FAILED"
        if "UNKNOWN" in code or "UNCONFIRMED" in code:
            return Outcome.UNKNOWN, {"code": code}
        actionable = {
            "CONFIGURATION_REQUIRED",
            "CONFIG_INVALID",
            "VERSION_CONFLICT",
            "PLAN_EXPIRED",
            "CONFIGURATION_CHANGED",
            "PLAN_ALREADY_USED",
            "LOCAL_CONFIRMATION_REQUIRED",
            "ROOT_CHANGED",
            "STATE_CHANGED",
            "ROOT_NOT_AUTHORIZED",
            "READ_ONLY",
            "FILE_NOT_FOUND",
            "EXTENSION_NOT_READY",
        }
        return (Outcome.NEEDS_USER_ACTION if code in actionable else Outcome.PERMANENT), {
            "code": code,
        }
    if isinstance(exc, PermissionError):
        return Outcome.PERMANENT, {"code": "FILE_PERMISSION_DENIED"}
    if isinstance(exc, (TypeError, ValueError)):
        return Outcome.PERMANENT, {"code": "FILE_INPUT_INVALID"}
    # Unexpected mutation failures may have happened after replacement. Do not
    # infer rollback or success; retain the durable plan for manual inspection.
    return (Outcome.UNKNOWN if mutation else Outcome.PERMANENT), {
        "code": "FILE_RESULT_UNCONFIRMED" if mutation else "FILE_OPERATION_FAILED",
    }


def _target(query: ContextQuery) -> tuple[str, str] | None:
    filters = query.filters
    if "root_key" in filters or "path" in filters:
        key, path = filters.get("root_key"), filters.get("path")
        if isinstance(key, str) and isinstance(path, str):
            return key, path
        return None
    try:
        parsed = urlsplit(query.text)
        if (
            parsed.scheme != "files"
            or parsed.query
            or parsed.fragment
            or not parsed.netloc
            or "@" in parsed.netloc
            or ":" in parsed.netloc
            or not parsed.path.startswith("/")
            or re.search(r"%(?![a-fA-F0-9]{2})", parsed.path)
        ):
            return None
        return parsed.netloc, unquote(parsed.path[1:], errors="strict")
    except (ValueError, UnicodeError):
        return None


class PersonalFilesExtension:
    def __init__(self) -> None:
        self._runtime: RuntimeContext | None = None
        self._service: FileService | None = None
        self._draining = False

    async def initialize(self, runtime: RuntimeContext) -> ExtensionInfo:
        self._runtime = runtime
        self._service = None
        self._draining = False
        return ExtensionInfo(
            runtime.extension_id,
            runtime.extension_version,
            PROTOCOL_VERSION,
            ("ToolProvider", "ContextProvider"),
            runtime.manifest_schema_hash,
        )

    async def health(self) -> HealthReport:
        ready = self._runtime is not None and not self._draining
        if ready and self._runtime and "workspace_binding" in self._runtime.non_secret_config:
            try:
                self._files()
            except FilesError as exc:
                return HealthReport(False, "workspace_unavailable", {"code": exc.code})
        return HealthReport(ready, "ready" if ready else "not_ready")

    async def drain(self, deadline: float) -> DrainReport:
        self._draining = True
        # File operations are synchronous and bounded: no detached background
        # thread can still mutate a file after this coroutine resumes.
        return DrainReport(True, 0)

    async def shutdown(self) -> None:
        self._draining = True
        self._service = None
        self._runtime = None

    def tools(self) -> tuple[ToolDescriptor, ...]:
        return tuple(
            ToolDescriptor(
                "files." + name,
                RiskLevel.INTERNAL_WRITE if name in {"preview", "write"} else RiskLevel.READ,
                spec,
                _OUTPUT,
                _DESCRIPTIONS[name],
            )
            for name, spec in _INPUTS.items()
        )

    def _files(self) -> FileService:
        if self._runtime is None or self._draining:
            raise FilesError("EXTENSION_NOT_READY")
        if self._service is None:
            try:
                config = workspace_config(self._runtime)
            except WorkspaceConfigurationError as exc:
                raise FilesError(exc.code) from None
            self._service = FileService(config)
        return self._service

    async def invoke(
        self, tool_id: str, arguments: dict[str, JsonValue], context: InvocationContext
    ) -> ToolResult:
        if self._runtime is None or self._draining:
            return ToolResult(Outcome.RETRYABLE, {"code": "EXTENSION_NOT_READY"})
        name = tool_id.removeprefix("files.")
        if tool_id != "files." + name or name not in _INPUTS:
            return ToolResult(Outcome.PERMANENT, {"code": "TOOL_NOT_FOUND"})
        if not isinstance(arguments, dict) or not _valid_arguments(name, arguments):
            return ToolResult(Outcome.PERMANENT, {"code": "FILE_INPUT_INVALID"})
        if not _deadline(context):
            return ToolResult(Outcome.PERMANENT, {"code": "DEADLINE_EXPIRED"})
        mutation = name in {"preview", "write"}
        if mutation and (
            not isinstance(context.idempotency_key, str)
            or not 1 <= len(context.idempotency_key) <= 256
        ):
            return ToolResult(Outcome.PERMANENT, {"code": "IDEMPOTENCY_KEY_REQUIRED"})
        try:
            files = self._files()
            if not _deadline(context):
                return ToolResult(Outcome.PERMANENT, {"code": "DEADLINE_EXPIRED"})
            args = cast(dict[str, Any], arguments)
            if name == "roots":
                output = files.roots()
            elif name == "list":
                output = files.list(
                    args["root_key"],
                    args.get("path", ""),
                    offset=args.get("offset", 0),
                    limit=args.get("limit", 100),
                )
            elif name == "read":
                output = files.read(
                    args["root_key"],
                    args["path"],
                    offset=args.get("offset", 0),
                    limit=args.get("limit", 65536),
                )
            elif name == "preview":
                output = files.preview(
                    args["operation"],
                    args["root_key"],
                    args["path"],
                    args["expected_sha256"],
                    args.get("content"),
                    idempotency_key=context.idempotency_key,
                )
            elif name == "write":
                output = files.write(
                    args["plan_id"], args["plan_hash"], idempotency_key=context.idempotency_key
                )
            else:
                output = files.status(args["plan_id"])
            outcome = Outcome.UNKNOWN if output.get("status") == "UNKNOWN" else Outcome.SUCCEEDED
            return ToolResult(outcome, cast(dict[str, JsonValue], output))
        except Exception as exc:
            outcome, output = _error(exc, mutation=mutation)
            return ToolResult(outcome, cast(dict[str, JsonValue], output))

    def _evidence(self, text: str, uri: str, *, kind: str, digest: str | None = None) -> Evidence:
        assert self._runtime is not None
        digest = digest or hashlib.sha256(text.encode("utf-8")).hexdigest()
        return Evidence(
            text,
            "files:" + hashlib.sha256(uri.encode()).hexdigest(),
            digest,
            uri,
            {
                "status": "CURRENT",
                "sensitivity": "SENSITIVE",
                "trust": "USER_SOURCE",
                "extension_id": self._runtime.extension_id,
                "extension_version": self._runtime.extension_version,
                "source_version": digest,
                "locator": {"kind": kind},
            },
        )

    def _status_evidence(self, code: str) -> Evidence:
        return self._evidence(
            json.dumps({"kind": "files_status", "code": code}),
            "files-status://instance/status",
            kind="status",
        )

    async def retrieve(self, query: ContextQuery) -> tuple[Evidence, ...]:
        if query.limit <= 0 or not query.text.strip() or self._runtime is None or self._draining:
            return ()
        selected = query.filters.get("provider_ids", [])
        if not isinstance(selected, (list, tuple)) or any(
            not isinstance(item, str) or item not in _PROVIDER_IDS for item in selected
        ):
            return (self._status_evidence("CONTEXT_PROVIDER_INVALID"),)
        if not selected:
            return ()
        if set(query.filters) - {"provider_ids", "root_key", "path"}:
            return (self._status_evidence("CONTEXT_FILTER_INVALID"),)
        evidence: list[Evidence] = []
        target = _target(query)
        try:
            files = self._files()
            if "files.document" in selected:
                if target is None or not target[1]:
                    evidence.append(self._status_evidence("EXACT_FILE_URI_REQUIRED"))
                else:
                    result = files.read(*target, offset=0, limit=262144)
                    if result.get("next_offset") is not None:
                        evidence.append(self._status_evidence("DOCUMENT_INCOMPLETE"))
                    elif not isinstance(result.get("content"), str):
                        evidence.append(self._status_evidence("DOCUMENT_UNAVAILABLE"))
                    else:
                        body = str(result["content"])
                        uri = "files://" + target[0] + "/" + quote(target[1], safe="/")
                        evidence.append(
                            self._evidence(
                                body or "[Empty UTF-8 file]",
                                uri,
                                kind="file_body",
                                digest=result["sha256"],
                            )
                        )
            if "files.workspace" in selected and len(evidence) < query.limit:
                summary = (
                    files.roots()
                    if target is None
                    else files.list(
                        target[0],
                        target[1].removesuffix("/"),
                        offset=0,
                        limit=100,
                    )
                )
                evidence.append(
                    self._evidence(
                        json.dumps(summary, ensure_ascii=False),
                        "files-workspace://instance/overview",
                        kind="directory_metadata",
                    )
                )
        except Exception as exc:
            _, error = _error(exc, mutation=False)
            evidence.append(self._status_evidence(error["code"]))
        return tuple(evidence[: query.limit])


def create_extension() -> PersonalFilesExtension:
    return PersonalFilesExtension()


if __name__ == "__main__":
    run_stdio_worker(create_extension)
