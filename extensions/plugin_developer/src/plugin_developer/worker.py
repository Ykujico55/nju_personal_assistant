"""Agent tools for candidate development; local owner controls execution and activation."""
from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from personal_assistant_sdk import (
    PROTOCOL_VERSION,
    DrainReport,
    ExtensionInfo,
    HealthReport,
    InvocationContext,
    Outcome,
    RiskLevel,
    RuntimeContext,
    ToolDescriptor,
    ToolResult,
)
from personal_assistant_sdk.models import JsonValue
from personal_assistant_sdk.worker import run_stdio_worker

from .control import ControlError, ControlStore
from .validation import ValidationError, run_validation, static_review
from .workspace import Workspace, WorkspaceError


def text(maximum: int, *, minimum: int = 1) -> dict[str, Any]:
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def obj(fields: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object", "properties": fields, "required": list(required),
        "additionalProperties": False,
    }


CANDIDATE = {"type": "string", "pattern": "^cand_[a-f0-9]{32}$"}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
PATH = text(256)
OUTPUT = {"type": "object", "additionalProperties": True}
REFERENCE = {"candidate_id": CANDIDATE}
SNAPSHOT = {**REFERENCE, "candidate_hash": HASH}
SCHEMAS: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "developer.workspace.create": (
        obj({
            "extension_id": {
                "type": "string", "pattern": "^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$",
                "maxLength": 128,
            },
            "version": text(64),
        }, ("extension_id",)),
        OUTPUT,
    ),
    "developer.candidates.list": (obj({}), OUTPUT),
    "developer.files.list": (obj(REFERENCE, ("candidate_id",)), OUTPUT),
    "developer.files.read": (
        obj({
            **REFERENCE, "path": PATH,
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 32000},
        }, ("candidate_id", "path")),
        OUTPUT,
    ),
    "developer.files.write": (
        obj({
            **REFERENCE, "path": PATH, "content": text(131072, minimum=0),
            "expected_sha256": HASH,
        }, ("candidate_id", "path", "content")),
        OUTPUT,
    ),
    "developer.review": (obj(REFERENCE, ("candidate_id",)), OUTPUT),
    "developer.validate": (obj(SNAPSHOT, ("candidate_id", "candidate_hash")), OUTPUT),
    "developer.validation.status": (obj(REFERENCE, ("candidate_id",)), OUTPUT),
    "developer.release.request": (obj(SNAPSHOT, ("candidate_id", "candidate_hash")), OUTPUT),
}
WRITES = {
    "developer.workspace.create", "developer.files.write", "developer.validate",
    "developer.release.request",
}
_DESCRIPTIONS = {
    "developer.workspace.create": "Create a candidate in the explicitly configured workspace.",
    "developer.files.write": "Write generated code with exact previous SHA-256; never host code.",
    "developer.review": "Read candidate changes, material hash and non-executing static review.",
    "developer.validate": (
        "Run fixed tests only after local owner authorization; never self-approve."
    ),
    "developer.release.request": "Prepare frozen material for human-confirmed local installation.",
}
_VALIDATION_KEYS = {
    "status", "candidate_hash", "tests_run", "failures", "errors", "skipped", "exit_code",
    "duration_seconds", "failed_tests", "log_name", "stdout_truncated", "stderr_truncated",
    "code", "static_errors",
}


def response(outcome: Outcome, value: Mapping[str, Any]) -> ToolResult:
    return ToolResult(outcome, output=cast(Mapping[str, JsonValue], dict(value)))


def _public_value(value: Any, *, depth: int = 0) -> Any:
    """Bound metadata; private authority values and raw process output stay local."""
    if depth > 8:
        return None
    if isinstance(value, Mapping):
        blocked = {
            "run_token", "token", "confirmation_nonce", "approved_digest", "log_path",
            "stdout", "stderr", "workspace_root", "source_path",
        }
        return {
            str(key): _public_value(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
            if str(key) not in blocked
        }
    if isinstance(value, (list, tuple)):
        return [_public_value(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return None


def _validation_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _public_value(item)
        for key, item in value.items()
        if key in _VALIDATION_KEYS
    }


def _validation_budget(
    deadline: object, configured: object, *, now: datetime | None = None,
) -> float:
    """Leave time for Job/process/stream cleanup before the host RPC deadline."""
    if not isinstance(deadline, str) or len(deadline) > 128:
        raise ControlError("VALIDATION_DEADLINE_INVALID")
    try:
        expires = datetime.fromisoformat(deadline)
    except ValueError:
        raise ControlError("VALIDATION_DEADLINE_INVALID") from None
    if expires.tzinfo is None or expires.utcoffset() != timedelta(0):
        raise ControlError("VALIDATION_DEADLINE_INVALID")
    moment = now if now is not None else datetime.now(UTC)
    available = (expires - moment).total_seconds() - 10.0
    if available < 1.0:
        raise ControlError("VALIDATION_DEADLINE_TOO_SHORT")
    selected = configured if type(configured) is int and 1 <= configured <= 120 else 30
    return min(float(selected), available)


class PluginDeveloperExtension:
    def __init__(self) -> None:
        self._runtime: RuntimeContext | None = None
        self._workspace_instance: Workspace | None = None
        self._active = 0
        self._draining = False

    async def initialize(self, runtime: RuntimeContext) -> ExtensionInfo:
        self._runtime = runtime
        self._workspace_instance = None
        self._draining = False
        return ExtensionInfo(
            runtime.extension_id, runtime.extension_version, PROTOCOL_VERSION,
            ("ToolProvider",), runtime.manifest_schema_hash,
        )

    async def health(self) -> HealthReport:
        return HealthReport(
            healthy=self._runtime is not None and not self._draining,
            status="draining" if self._draining else "ready",
        )

    async def drain(self, deadline: float) -> DrainReport:
        del deadline
        self._draining = True
        return DrainReport(self._active == 0, self._active)

    async def shutdown(self) -> None:
        self._draining = True
        self._runtime = None
        self._workspace_instance = None

    def tools(self) -> tuple[ToolDescriptor, ...]:
        return tuple(
            ToolDescriptor(
                name, RiskLevel.INTERNAL_WRITE if name in WRITES else RiskLevel.READ,
                schemas[0], schemas[1], description=_DESCRIPTIONS.get(name, ""),
            )
            for name, schemas in SCHEMAS.items()
        )

    def _workspace(self) -> Workspace:
        if self._workspace_instance is None:
            config = self._runtime.non_secret_config if self._runtime else {}
            path = config.get("workspace_root")
            if not isinstance(path, str) or not path:
                raise WorkspaceError("WORKSPACE_CONFIGURATION_REQUIRED")
            self._workspace_instance = Workspace(Path(path))
        return self._workspace_instance

    def _control(self) -> ControlStore:
        return ControlStore(self._workspace())

    async def invoke(
        self, tool_id: str, arguments: dict[str, Any], context: InvocationContext,
    ) -> ToolResult:
        if self._runtime is None or self._draining:
            return response(Outcome.RETRYABLE, {"code": "EXTENSION_NOT_READY"})
        if tool_id not in SCHEMAS:
            return response(Outcome.PERMANENT, {"code": "UNKNOWN_TOOL"})
        self._active += 1
        try:
            return await self._invoke(tool_id, arguments, context)
        except (WorkspaceError, ValidationError, ControlError) as exc:
            needs_owner = any(
                word in exc.code for word in (
                    "CONFIGURATION", "APPROVAL", "UNCONFIRMED", "REQUIRED", "BUSY",
                    "EXPIRED", "EXHAUSTED", "POLICY_CHANGED",
                )
            )
            return response(
                Outcome.NEEDS_USER_ACTION if needs_owner else Outcome.PERMANENT,
                {"code": exc.code},
            )
        except (ValueError, TypeError, KeyError):
            return response(Outcome.PERMANENT, {"code": "INVALID_INPUT"})
        except OSError:
            return response(Outcome.PERMANENT, {"code": "WORKSPACE_UNAVAILABLE"})
        finally:
            self._active -= 1

    async def _invoke(
        self, name: str, args: dict[str, Any], context: InvocationContext,
    ) -> ToolResult:
        workspace = self._workspace()
        if name == "developer.workspace.create":
            identifier = workspace.create(args["extension_id"], args.get("version", "0.1.0"))
            return response(Outcome.SUCCEEDED, {"candidate_id": identifier})
        if name == "developer.candidates.list":
            return response(Outcome.SUCCEEDED, {"candidates": workspace.list_candidates()})
        identifier = args["candidate_id"]
        if name == "developer.files.list":
            return response(Outcome.SUCCEEDED, {"files": workspace.tree(identifier)})
        if name == "developer.files.read":
            content = workspace.read(identifier, args["path"])
            offset, limit = args.get("offset", 0), args.get("limit", 16000)
            if (
                type(offset) is not int or offset < 0
                or type(limit) is not int or not 1 <= limit <= 32000
            ):
                raise ValueError("invalid range")
            end = min(len(content), offset + limit)
            return response(Outcome.SUCCEEDED, {
                "path": args["path"], "content": content[offset:end],
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "total_characters": len(content),
                "next_offset": end if end < len(content) else None,
            })
        if name == "developer.files.write":
            metadata = workspace.write(
                identifier, args["path"], args["content"],
                expected_sha256=args.get("expected_sha256"),
            )
            return response(Outcome.SUCCEEDED, metadata)
        if name == "developer.review":
            review = workspace.review(identifier)
            budget, diffs, truncated = 24000, [], False
            for item in review.get("diff", []):
                original = str(item.get("diff", ""))
                clipped = original[:budget]
                budget -= len(clipped)
                truncated = truncated or len(clipped) != len(original)
                diffs.append({"path": item.get("path"), "diff": clipped})
            manifest = str(review.get("manifest", ""))
            return response(Outcome.SUCCEEDED, {
                "candidate_id": identifier, "candidate_hash": review["candidate_hash"],
                "extension_id": review["extension_id"], "version": review["version"],
                "files": review["files"], "manifest": manifest[:8000], "diff": diffs,
                "truncated": truncated or len(manifest) > 8000,
                "static_review": _public_value(static_review(workspace.candidate_path(identifier))),
            })
        control = self._control()
        if name == "developer.validation.status":
            return response(Outcome.SUCCEEDED, cast(dict[str, Any], _public_value(
                control.status(identifier)
            )))
        digest = args["candidate_hash"]
        review = workspace.review(identifier)
        if review["candidate_hash"] != digest:
            raise WorkspaceError("CANDIDATE_CHANGED")
        if name == "developer.release.request":
            if control.latest_pass(identifier, digest) is None:
                return response(Outcome.NEEDS_USER_ACTION, {"code": "VALIDATION_REQUIRED"})
            workspace.export(identifier, digest)
            return response(Outcome.NEEDS_USER_ACTION, {
                "status": "READY_FOR_LOCAL_INSTALL", "candidate_id": identifier,
                "candidate_hash": digest, "extension_id": review["extension_id"],
                "version": review["version"], "install_confirmation_required": True,
            })
        config = self._runtime.non_secret_config if self._runtime else {}
        timeout = _validation_budget(
            context.deadline, config.get("validation_timeout_seconds", 30)
        )
        authorization = control.authorize_run(identifier, digest, context.idempotency_key)
        if authorization["state"] == "CACHED":
            summary = _validation_summary(authorization["result"])
        else:
            token = authorization["run_token"]
            cancelled = threading.Event()
            running = asyncio.create_task(asyncio.to_thread(
                run_validation, workspace.candidate_path(identifier),
                expected_digest=digest, approved_digest=digest, timeout_seconds=timeout,
                log_directory=workspace.root / "validation-logs", cancel_event=cancelled,
            ))
            try:
                raw = await asyncio.shield(running)
            except asyncio.CancelledError as cancellation:
                cancelled.set()
                # Preserve cancellation even if cleanup fails. Repeated cancels
                # must not detach the thread before the process tree is reaped.
                while True:
                    try:
                        raw = await asyncio.shield(running)
                        break
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        raw = {
                            "status": "UNKNOWN", "candidate_hash": digest,
                            "code": "VALIDATION_RESULT_UNCONFIRMED",
                        }
                        break
                try:
                    control.record_result(
                        identifier, digest, context.idempotency_key, token,
                        _validation_summary(raw),
                    )
                finally:
                    # A failed record write leaves RUNNING, which also blocks
                    # retry/promotion. Never convert cancellation into success.
                    raise cancellation
            except Exception:
                raw = {
                    "status": "UNKNOWN", "candidate_hash": digest,
                    "code": "VALIDATION_RESULT_UNCONFIRMED",
                }
            summary = _validation_summary(raw)
            try:
                control.record_result(identifier, digest, context.idempotency_key, token, summary)
            except ControlError:
                return response(Outcome.UNKNOWN, {
                    "status": "UNKNOWN", "candidate_hash": digest,
                    "code": "VALIDATION_RESULT_UNCONFIRMED",
                })
        status = summary.get("status")
        outcome = Outcome.SUCCEEDED if status == "PASSED" else (
            Outcome.UNKNOWN if status == "UNKNOWN" else Outcome.PERMANENT
        )
        return response(outcome, summary)


def create_extension() -> PluginDeveloperExtension:
    return PluginDeveloperExtension()


if __name__ == "__main__":
    run_stdio_worker(create_extension)
