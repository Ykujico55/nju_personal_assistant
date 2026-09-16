"""PostgreSQL side-effect outbox with atomic approval consumption/finalization."""

from __future__ import annotations

import hmac
from typing import Any

import asyncpg

from personal_assistant.core.approvals.service import (
    ApprovalBindingMismatchError,
    ApprovalExpiredError,
    ApprovalStateError,
)
from personal_assistant.core.jobs.outbox import SideEffectIntent, SideEffectState
from personal_assistant.domain import (
    ConcurrentModificationError,
    NotFoundError,
    ValidationError,
)
from personal_assistant.domain.enums import ApprovalState

from .connection import PostgresDatabase

_APPROVED = ApprovalState.APPROVED.value
_EXECUTING = ApprovalState.EXECUTING.value
_TERMINAL_APPROVAL = {SideEffectState.SUCCEEDED, SideEffectState.FAILED, SideEffectState.UNKNOWN}


class PostgresSideEffectOutbox:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    async def create_with_approval_consumption(self, intent: SideEffectIntent) -> None:
        """Consume the APPROVED approval and insert the intent in one transaction.

        Enforces every part of the exact-binding contract:

        - the approval must still be ``APPROVED`` and unexpired;
        - the complete ``action_fingerprint`` must match (and is mandatory);
        - the task binding must match;
        - a drifted approval is atomically cancelled and never executed;
        - an expired approval is atomically expired and never executed;
        - a duplicate ``(tool_id, idempotency_key)`` rolls the whole unit back,
          leaving the approval untouched.
        """

        if not intent.action_fingerprint:
            raise ValidationError(
                "side-effect intent requires the complete action fingerprint"
            )

        outcome = "execute"
        async with self._db.transaction(), self._db.connection() as connection:
            approval = await connection.fetchrow(
                "SELECT status, task_id, action_fingerprint, version, "
                "(now() >= expires_at) AS expired "
                "FROM approvals WHERE id = $1 FOR UPDATE",
                intent.approval_id,
            )
            if approval is None:
                raise NotFoundError(f"approval not found: {intent.approval_id}")
            if approval["status"] != _APPROVED:
                raise ApprovalStateError(
                    f"approval {intent.approval_id} is {approval['status']}, not approved"
                )
            stored = approval["action_fingerprint"]
            stored = stored.strip() if isinstance(stored, str) else ""
            drifted = not hmac.compare_digest(
                stored, intent.action_fingerprint
            ) or approval["task_id"] != intent.task_id
            if approval["expired"]:
                await self._burn(
                    connection,
                    intent.approval_id,
                    ApprovalState.EXPIRED.value,
                    failure_reason="approval expired",
                    completed_at_sql="expires_at",
                )
                outcome = "expired"
            elif drifted:
                await self._burn(
                    connection,
                    intent.approval_id,
                    ApprovalState.CANCELLED.value,
                    failure_reason="approved action changed after preview",
                    completed_at_sql="now()",
                )
                outcome = "drift"
            else:
                consumed = await connection.execute(
                    "UPDATE approvals SET status = $2, consumed_at = now(), "
                    "version = version + 1 WHERE id = $1 AND version = $3",
                    intent.approval_id,
                    _EXECUTING,
                    approval["version"],
                )
                if consumed == "UPDATE 0":
                    raise ConcurrentModificationError(
                        f"approval {intent.approval_id} changed while being consumed"
                    )
                try:
                    await connection.execute(
                        """
                        INSERT INTO side_effect_intents (
                            id, task_id, tool_id, idempotency_key, approval_id,
                            canonical_payload_sha256, action_fingerprint, state,
                            receipt, created_at
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        """,
                        intent.id,
                        intent.task_id,
                        intent.tool_id,
                        intent.idempotency_key,
                        intent.approval_id,
                        intent.canonical_payload_sha256,
                        intent.action_fingerprint,
                        intent.state.value,
                        dict(intent.receipt),
                        intent.created_at,
                    )
                except asyncpg.UniqueViolationError as exc:
                    # Rolls back the approval consumption above.
                    raise ValidationError(
                        "side-effect intent idempotency key was reused"
                    ) from exc

        if outcome == "expired":
            raise ApprovalExpiredError(
                f"approval expired: {intent.approval_id}"
            )
        if outcome == "drift":
            raise ApprovalBindingMismatchError(
                "target, payload, attachment or extension version changed"
            )

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
        """Finalize approval and intent in one transaction (no split commit)."""

        if state not in _TERMINAL_APPROVAL:
            raise ValidationError("finalize requires a terminal side-effect state")
        approval_status = state.value
        async with self._db.transaction(), self._db.connection() as connection:
            approval = await connection.fetchrow(
                "SELECT status, version FROM approvals WHERE id = $1 FOR UPDATE",
                approval_id,
            )
            if approval is None:
                raise NotFoundError(f"approval not found: {approval_id}")
            if approval["status"] != _EXECUTING:
                raise ApprovalStateError(
                    f"approval {approval_id} is {approval['status']}, not executing"
                )
            intent = await connection.fetchrow(
                "SELECT state, approval_id FROM side_effect_intents "
                "WHERE id = $1 FOR UPDATE",
                intent_id,
            )
            if intent is None:
                raise NotFoundError(f"side-effect intent not found: {intent_id}")
            if intent["approval_id"] != approval_id:
                raise ValidationError("intent and approval do not belong together")
            if SideEffectState(intent["state"]).terminal:
                raise ValidationError(
                    f"side-effect intent {intent_id} is already finalized"
                )

            if state is SideEffectState.SUCCEEDED:
                await connection.execute(
                    "UPDATE side_effect_intents SET state = $2, receipt = $3, "
                    "diagnostic_code = NULL, updated_at = now() WHERE id = $1",
                    intent_id,
                    state.value,
                    dict(receipt or {}),
                )
            else:
                await connection.execute(
                    "UPDATE side_effect_intents SET state = $2, diagnostic_code = $3, "
                    "updated_at = now() WHERE id = $1",
                    intent_id,
                    state.value,
                    diagnostic_code or failure_reason,
                )
            updated = await connection.execute(
                "UPDATE approvals SET status = $2, version = version + 1, "
                "completed_at = now(), result_reference = $3, failure_reason = $4 "
                "WHERE id = $1 AND version = $5",
                approval_id,
                approval_status,
                result_reference,
                failure_reason,
                approval["version"],
            )
            if updated == "UPDATE 0":
                raise ConcurrentModificationError(
                    f"approval {approval_id} changed while being finalized"
                )

    async def mark_succeeded(self, intent_id: str, receipt: dict[str, Any]) -> None:
        await self._finish(intent_id, SideEffectState.SUCCEEDED.value, receipt=dict(receipt))

    async def mark_failed(self, intent_id: str, error_code: str) -> None:
        await self._finish(intent_id, SideEffectState.FAILED.value, diagnostic=error_code)

    async def mark_unknown(self, intent_id: str, diagnostic_code: str) -> None:
        await self._finish(
            intent_id, SideEffectState.UNKNOWN.value, diagnostic=diagnostic_code
        )

    async def _burn(
        self,
        connection: asyncpg.Connection,
        approval_id: str,
        status: str,
        *,
        failure_reason: str,
        completed_at_sql: str,
    ) -> None:
        await connection.execute(
            f"UPDATE approvals SET status = $2, version = version + 1, "
            f"completed_at = {completed_at_sql}, failure_reason = $3, "
            "result_reference = NULL WHERE id = $1",
            approval_id,
            status,
            failure_reason,
        )

    async def _finish(
        self,
        intent_id: str,
        state: str,
        *,
        receipt: dict[str, Any] | None = None,
        diagnostic: str | None = None,
    ) -> None:
        async with self._db.transaction(), self._db.connection() as connection:
            if receipt is None:
                row = await connection.fetchrow(
                    "UPDATE side_effect_intents SET state = $2, diagnostic_code = $3, "
                    "updated_at = now() WHERE id = $1 RETURNING id",
                    intent_id,
                    state,
                    diagnostic,
                )
            else:
                row = await connection.fetchrow(
                    "UPDATE side_effect_intents SET state = $2, receipt = $3, "
                    "diagnostic_code = $4, updated_at = now() WHERE id = $1 RETURNING id",
                    intent_id,
                    state,
                    dict(receipt),
                    diagnostic,
                )
            if row is None:
                raise NotFoundError(f"side-effect intent not found: {intent_id}")
