"""In-memory outbox test double with real once-only approval semantics.

It is still not production persistence (no crash recovery, no transactions),
but unlike a bare intent store it validates the approval, enforces expiry and
the exact binding fingerprint, consumes the approval once, and refuses to reuse
a consumed approval even under a different idempotency key.
"""

from __future__ import annotations

import asyncio
import hmac
from copy import deepcopy
from dataclasses import replace
from typing import Any

from personal_assistant.core.approvals.service import (
    ApprovalBinding,
    ApprovalBindingMismatchError,
    ApprovalExpiredError,
    ApprovalRecord,
    ApprovalService,
    ApprovalStateError,
)
from personal_assistant.core.jobs.outbox import SideEffectIntent, SideEffectState
from personal_assistant.domain import AttachmentDigest, NotFoundError, ValidationError
from personal_assistant.domain.enums import ApprovalState


def _binding_from_record(record: ApprovalRecord) -> ApprovalBinding:
    action = record.action
    attachments = tuple(
        AttachmentDigest(
            name=item["name"], sha256=item["sha256"], size_bytes=item["size_bytes"]
        )
        for item in action.get("attachments") or ()
    )
    return ApprovalBinding(
        action_type=action["action_type"],
        task_id=action["task_id"],
        tool_id=action["tool_id"],
        tool_version=action["tool_version"],
        extension_id=action["extension_id"],
        extension_version=action["extension_version"],
        target=action.get("target"),
        payload=action.get("payload"),
        attachments=attachments,
        form_version=action.get("form_version"),
    )


class InMemorySideEffectOutbox:
    def __init__(self, approvals: ApprovalService) -> None:
        self._intents: dict[str, SideEffectIntent] = {}
        self._keys: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()
        self._approvals = approvals

    async def create_with_approval_consumption(self, intent: SideEffectIntent) -> None:
        if not intent.action_fingerprint:
            raise ValidationError(
                "side-effect intent requires the complete action fingerprint"
            )
        async with self._lock:
            key = (intent.tool_id, intent.idempotency_key)
            if key in self._keys:
                raise ValidationError(
                    "side-effect intent idempotency key was reused"
                )
            record = await self._approvals.get(intent.approval_id)
            if record.state is ApprovalState.EXPIRED:
                raise ApprovalExpiredError(
                    f"approval expired: {intent.approval_id}"
                )
            if record.state is not ApprovalState.APPROVED:
                raise ApprovalStateError(
                    f"approval {intent.approval_id} is {record.state.value}, not approved"
                )
            action = record.action
            drifted = not hmac.compare_digest(
                record.action_fingerprint, intent.action_fingerprint
            ) or action.get("task_id") != intent.task_id
            if drifted:
                await self._approvals.reject(
                    intent.approval_id,
                    reason="approved action changed after preview",
                )
                raise ApprovalBindingMismatchError(
                    "target, payload, attachment or extension version changed"
                )
            binding = _binding_from_record(record)
            await self._approvals.consume_for_execution(intent.approval_id, binding)
            self._keys.add(key)
            self._intents[intent.id] = deepcopy(intent)

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
        await self._finish(
            intent_id,
            state,
            receipt=receipt,
            diagnostic=diagnostic_code or failure_reason,
        )
        if state is SideEffectState.SUCCEEDED:
            await self._approvals.mark_succeeded(
                approval_id, result_reference=result_reference or ""
            )
        elif state is SideEffectState.FAILED:
            await self._approvals.mark_failed(
                approval_id, reason=failure_reason or "failed"
            )
        else:
            await self._approvals.mark_unknown(
                approval_id, reference_id=result_reference or "unknown"
            )

    async def mark_succeeded(self, intent_id: str, receipt: dict[str, Any]) -> None:
        await self._finish(
            intent_id, SideEffectState.SUCCEEDED, receipt=dict(receipt)
        )

    async def mark_failed(self, intent_id: str, error_code: str) -> None:
        await self._finish(intent_id, SideEffectState.FAILED, diagnostic=error_code)

    async def mark_unknown(self, intent_id: str, diagnostic_code: str) -> None:
        await self._finish(intent_id, SideEffectState.UNKNOWN, diagnostic=diagnostic_code)

    async def _finish(
        self,
        intent_id: str,
        state: SideEffectState,
        *,
        receipt: dict[str, Any] | None = None,
        diagnostic: str | None = None,
    ) -> None:
        async with self._lock:
            intent = self._intents.get(intent_id)
            if intent is None:
                raise NotFoundError(f"side-effect intent not found: {intent_id}")
            self._intents[intent_id] = replace(
                intent,
                state=state,
                receipt=receipt if receipt is not None else intent.receipt,
            )
            del diagnostic
