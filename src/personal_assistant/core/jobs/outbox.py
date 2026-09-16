"""Durable intent contract for effectively-once external actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class SideEffectState(StrEnum):
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"

    @property
    def terminal(self) -> bool:
        return self in {
            SideEffectState.SUCCEEDED,
            SideEffectState.FAILED,
            SideEffectState.UNKNOWN,
        }


@dataclass(frozen=True, slots=True)
class SideEffectIntent:
    id: str
    task_id: str
    tool_id: str
    idempotency_key: str
    approval_id: str
    canonical_payload_sha256: str
    state: SideEffectState
    created_at: datetime
    # Complete approval-envelope fingerprint. Required: the outbox must verify it
    # against the stored approval before consuming, so target/payload/attachment
    # or extension-version drift cannot execute and cannot be bypassed by
    # omitting the field.
    action_fingerprint: str
    receipt: dict[str, Any] = field(default_factory=dict)


class SideEffectOutboxPort(Protocol):
    async def create_with_approval_consumption(self, intent: SideEffectIntent) -> None:
        """Persist intent and consume approval in one database transaction.

        Must enforce approval expiry and the complete ``action_fingerprint``
        binding. A drifted approval is atomically cancelled (never executed) and
        raises ``ApprovalBindingMismatchError``; an expired approval is atomically
        expired and raises ``ApprovalExpiredError``.
        """

    async def finalize(
        self,
        intent_id: str,
        approval_id: str,
        *,
        state: SideEffectState,
        result_reference: str | None = None,
        failure_reason: str | None = None,
        diagnostic_code: str | None = None,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        """Atomically record the terminal result on both approval and intent.

        This is the single finalization boundary for an executed external
        action: a crash can leave the pair unresolved (EXECUTING/PREPARED) but
        never contradictory.
        """

    async def mark_succeeded(self, intent_id: str, receipt: dict[str, Any]) -> None: ...

    async def mark_failed(self, intent_id: str, error_code: str) -> None: ...

    async def mark_unknown(self, intent_id: str, diagnostic_code: str) -> None: ...
