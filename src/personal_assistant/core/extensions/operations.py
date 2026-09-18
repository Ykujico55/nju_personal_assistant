"""Durable extension-operation state and its persistence port.

Operation rows answer ``GET /admin/v1/extension-operations/{id}``, let a
restarted supervisor mark interrupted work as failed, and enforce idempotent
command replay per extension.  Diagnostics are restricted to a small allowlist
of codes; no raw third-party message, path, or stack trace is persisted here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

OPERATION_KINDS = (
    "install",
    "upgrade",
    "enable",
    "disable",
    "rollback",
    "uninstall",
    "recover",
    "quarantine",
)


class OperationState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


TERMINAL_OPERATION_STATES = frozenset(
    {OperationState.SUCCEEDED, OperationState.FAILED}
)

DIAGNOSTIC_CODES = frozenset(
    {
        "CONFIRMATION_REQUIRED",
        "PLAN_NOT_FOUND",
        "PLAN_MODE_MISMATCH",
        "PLAN_BASELINE_CHANGED",
        "MANIFEST_INVALID",
        "STATE_CONFLICT",
        "EXTENSION_NOT_FOUND",
        "EXTENSION_NOT_ENABLED",
        "CAPABILITY_NOT_PUBLISHED",
        "INSTALL_CONFLICT",
        "INSTALL_PATH_MISSING",
        "INSTALL_FAILED",
        "UPGRADE_FAILED",
        "ENABLE_FAILED",
        "DISABLE_FAILED",
        "DRAIN_TIMEOUT",
        "UNINSTALL_FAILED",
        "UNINSTALL_PATH_UNSAFE",
        "ROLLBACK_FAILED",
        "ROLLBACK_INCOMPATIBLE",
        "ROLLBACK_NO_CANDIDATE",
        "HEALTHCHECK_FAILED",
        "REQUIRED_CAPABILITY_UNAVAILABLE",
        "HANDSHAKE_MISMATCH",
        "WORKER_RPC_ERROR",
        "WORKER_TIMEOUT",
        "WORKER_CRASHED",
        "PURGE_NOT_IMPLEMENTED",
        "OPERATION_NOT_FOUND",
        "OPERATION_EXISTS",
        "OPERATION_CANCELLED",
        "OPERATION_FAILED",
        "OPERATION_IN_PROGRESS",
        "IDEMPOTENCY_CONFLICT",
        "RECOVERY_FAILED",
        "SUPERVISOR_RESTART",
    }
)


def validate_diagnostic_code(diagnostic_code: str | None) -> None:
    """Stores must call this before writing so no raw text can reach the database."""

    if diagnostic_code is not None and diagnostic_code not in DIAGNOSTIC_CODES:
        raise ValueError(
            f"diagnostic code is not on the safe allowlist: {diagnostic_code!r}"
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ExtensionOperation:
    id: str
    extension_id: str
    operation: str
    status: OperationState
    diagnostic_code: str | None = None
    idempotency_key: str | None = None
    command_fingerprint: str | None = None
    request_scope: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.operation not in OPERATION_KINDS:
            raise ValueError(f"unsupported extension operation: {self.operation!r}")
        if self.diagnostic_code is not None and self.diagnostic_code not in DIAGNOSTIC_CODES:
            raise ValueError(
                f"diagnostic code is not on the safe allowlist: {self.diagnostic_code!r}"
            )


class ExtensionOperationStore(Protocol):
    async def create(self, operation: ExtensionOperation) -> None: ...

    async def update(
        self,
        operation_id: str,
        *,
        status: OperationState,
        diagnostic_code: str | None = None,
    ) -> ExtensionOperation: ...

    async def get(self, operation_id: str) -> ExtensionOperation | None: ...

    async def find_by_request_scope(
        self,
        request_scope: str,
        idempotency_key: str,
    ) -> ExtensionOperation | None:
        """Durable replay lookup; must work without any in-process state."""

    async def interrupt_running(self, *, diagnostic_code: str) -> int:
        """Mark every non-terminal operation as failed after a supervisor restart."""
