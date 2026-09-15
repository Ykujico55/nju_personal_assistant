"""Prepare/approve/consume service for exact external side effects."""

from __future__ import annotations

import asyncio
import copy
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol, cast

from personal_assistant.domain.enums import ApprovalState
from personal_assistant.domain.errors import (
    AlreadyExistsError,
    ConcurrentModificationError,
    DomainError,
    NotFoundError,
    ValidationError,
)
from personal_assistant.domain.models import AttachmentDigest, utc_now

from .canonicalize import canonical_json, canonical_sha256, normalize_json
from .state_machine import ensure_approval_transition

MAX_APPROVAL_TTL = timedelta(minutes=5)


class ApprovalError(DomainError):
    code = "approval_error"


class ApprovalExpiredError(ApprovalError):
    code = "approval_expired"


class ApprovalStateError(ApprovalError):
    code = "approval_state_error"


class ApprovalBindingMismatchError(ApprovalError):
    code = "approval_binding_mismatch"


class InvalidApprovalNonceError(ApprovalError):
    code = "invalid_approval_nonce"


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """Every user-visible field whose change invalidates an approval."""

    action_type: str
    task_id: str
    tool_id: str
    tool_version: str
    extension_id: str
    extension_version: str
    target: Any
    payload: Any
    attachments: tuple[AttachmentDigest, ...] = ()
    form_version: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.action_type,
            self.task_id,
            self.tool_id,
            self.tool_version,
            self.extension_id,
            self.extension_version,
        )
        if any(not value or not value.strip() for value in required):
            raise ValidationError("approval binding identity fields are required")

    def envelope(self) -> dict[str, Any]:
        """Return the complete canonicalizable action snapshot."""

        return {
            "action_type": self.action_type,
            "task_id": self.task_id,
            "tool_id": self.tool_id,
            "tool_version": self.tool_version,
            "extension_id": self.extension_id,
            "extension_version": self.extension_version,
            "target": self.target,
            "payload": self.payload,
            "attachments": [
                {
                    "name": item.name,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in self.attachments
            ],
            "form_version": self.form_version,
        }


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: str
    state: ApprovalState
    action_fingerprint: str
    canonical_action: str
    nonce: str
    created_at: datetime
    expires_at: datetime
    version: int = 0
    approved_at: datetime | None = None
    approved_by: str | None = None
    consumed_at: datetime | None = None
    completed_at: datetime | None = None
    result_reference: str | None = None
    failure_reason: str | None = None

    @property
    def action(self) -> dict[str, Any]:
        # A new object prevents a caller from mutating the persisted snapshot.
        return cast(dict[str, Any], json.loads(self.canonical_action))


class ApprovalRepository(Protocol):
    """A production adapter must implement create and save atomically."""

    async def create(self, record: ApprovalRecord) -> None: ...

    async def get(self, approval_id: str) -> ApprovalRecord: ...

    async def save(
        self, record: ApprovalRecord, *, expected_version: int
    ) -> ApprovalRecord: ...


class InMemoryApprovalRepository:
    """CAS repository for tests and local development, not production storage."""

    def __init__(self) -> None:
        self._records: dict[str, ApprovalRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: ApprovalRecord) -> None:
        async with self._lock:
            if record.id in self._records:
                raise AlreadyExistsError(f"approval already exists: {record.id}")
            self._records[record.id] = copy.deepcopy(record)

    async def get(self, approval_id: str) -> ApprovalRecord:
        async with self._lock:
            try:
                return copy.deepcopy(self._records[approval_id])
            except KeyError as exc:
                raise NotFoundError(f"approval not found: {approval_id}") from exc

    async def save(
        self, record: ApprovalRecord, *, expected_version: int
    ) -> ApprovalRecord:
        async with self._lock:
            current = self._records.get(record.id)
            if current is None:
                raise NotFoundError(f"approval not found: {record.id}")
            if current.version != expected_version:
                raise ConcurrentModificationError(
                    f"approval {record.id} expected version {expected_version}, "
                    f"found {current.version}"
                )
            saved = replace(record, version=expected_version + 1)
            self._records[record.id] = copy.deepcopy(saved)
            return copy.deepcopy(saved)


class ApprovalService:
    """Deterministic approval policy shared by every R2 capability."""

    def __init__(self, repository: ApprovalRepository) -> None:
        self._repository = repository

    async def prepare(
        self,
        binding: ApprovalBinding,
        *,
        now: datetime | None = None,
        ttl: timedelta = MAX_APPROVAL_TTL,
    ) -> ApprovalRecord:
        current_time = now or utc_now()
        self._validate_aware(current_time)
        if ttl <= timedelta(0) or ttl > MAX_APPROVAL_TTL:
            raise ValidationError("approval ttl must be positive and at most 5 minutes")

        # Normalize once and persist the exact bytes shown in the preview.
        normalized = normalize_json(binding.envelope())
        encoded = canonical_json(normalized)
        record = ApprovalRecord(
            id=str(uuid.uuid4()),
            state=ApprovalState.WAITING_APPROVAL,
            action_fingerprint=canonical_sha256(normalized),
            canonical_action=encoded,
            nonce=secrets.token_urlsafe(32),
            created_at=current_time,
            expires_at=current_time + ttl,
        )
        await self._repository.create(record)
        return record

    async def get(
        self,
        approval_id: str,
        *,
        now: datetime | None = None,
        refresh_expiry: bool = True,
    ) -> ApprovalRecord:
        record = await self._repository.get(approval_id)
        current_time = now or utc_now()
        if refresh_expiry and self._is_expirable(record) and current_time >= record.expires_at:
            return await self._expire(record)
        return record

    async def approve(
        self,
        approval_id: str,
        *,
        nonce: str,
        actor_id: str,
        now: datetime | None = None,
    ) -> ApprovalRecord:
        current_time = now or utc_now()
        record = await self._repository.get(approval_id)
        if self._is_expirable(record) and current_time >= record.expires_at:
            await self._expire(record)
            raise ApprovalExpiredError(f"approval expired: {approval_id}")
        if record.state is not ApprovalState.WAITING_APPROVAL:
            raise ApprovalStateError(
                f"approval {approval_id} is {record.state.value}, not waiting"
            )
        if not actor_id.strip():
            raise ValidationError("actor_id is required")
        if not hmac.compare_digest(record.nonce, nonce):
            raise InvalidApprovalNonceError("approval nonce does not match")
        ensure_approval_transition(record.state, ApprovalState.APPROVED)
        updated = replace(
            record,
            state=ApprovalState.APPROVED,
            approved_at=current_time,
            approved_by=actor_id,
        )
        return await self._repository.save(updated, expected_version=record.version)

    async def reject(
        self,
        approval_id: str,
        *,
        now: datetime | None = None,
        reason: str | None = None,
    ) -> ApprovalRecord:
        record = await self._repository.get(approval_id)
        if record.state not in {
            ApprovalState.WAITING_APPROVAL,
            ApprovalState.APPROVED,
        }:
            raise ApprovalStateError(f"approval cannot be rejected from {record.state.value}")
        ensure_approval_transition(record.state, ApprovalState.CANCELLED)
        updated = replace(
            record,
            state=ApprovalState.CANCELLED,
            completed_at=now or utc_now(),
            failure_reason=reason or "rejected by user",
        )
        return await self._repository.save(updated, expected_version=record.version)

    async def consume_for_execution(
        self,
        approval_id: str,
        binding: ApprovalBinding,
        *,
        now: datetime | None = None,
    ) -> ApprovalRecord:
        """Atomically consume approval before invoking the external action.

        The database adapter's ``save`` must be a compare-and-swap in the same
        transaction that creates the action attempt/outbox row.
        """

        current_time = now or utc_now()
        record = await self._repository.get(approval_id)
        if self._is_expirable(record) and current_time >= record.expires_at:
            await self._expire(record)
            raise ApprovalExpiredError(f"approval expired: {approval_id}")
        if record.state is not ApprovalState.APPROVED:
            raise ApprovalStateError(
                f"approval {approval_id} is {record.state.value}, not approved"
            )

        presented = canonical_sha256(binding.envelope())
        if not hmac.compare_digest(record.action_fingerprint, presented):
            # Drift burns the approval: callers must prepare and display a fresh
            # preview instead of retrying with the old user decision.
            ensure_approval_transition(record.state, ApprovalState.CANCELLED)
            cancelled = replace(
                record,
                state=ApprovalState.CANCELLED,
                completed_at=current_time,
                failure_reason="approved action changed after preview",
            )
            await self._repository.save(cancelled, expected_version=record.version)
            raise ApprovalBindingMismatchError(
                "target, payload, attachment or extension version changed"
            )

        ensure_approval_transition(record.state, ApprovalState.EXECUTING)
        executing = replace(
            record,
            state=ApprovalState.EXECUTING,
            consumed_at=current_time,
        )
        return await self._repository.save(executing, expected_version=record.version)

    async def mark_succeeded(
        self, approval_id: str, *, result_reference: str, now: datetime | None = None
    ) -> ApprovalRecord:
        return await self._finish(
            approval_id,
            ApprovalState.SUCCEEDED,
            now=now,
            result_reference=result_reference,
        )

    async def mark_failed(
        self, approval_id: str, *, reason: str, now: datetime | None = None
    ) -> ApprovalRecord:
        return await self._finish(
            approval_id, ApprovalState.FAILED, now=now, failure_reason=reason
        )

    async def mark_unknown(
        self, approval_id: str, *, reference_id: str, now: datetime | None = None
    ) -> ApprovalRecord:
        """UNKNOWN is terminal here; only a separate reconciliation fact follows."""

        return await self._finish(
            approval_id,
            ApprovalState.UNKNOWN,
            now=now,
            result_reference=reference_id,
        )

    async def _finish(
        self,
        approval_id: str,
        target: ApprovalState,
        *,
        now: datetime | None,
        result_reference: str | None = None,
        failure_reason: str | None = None,
    ) -> ApprovalRecord:
        record = await self._repository.get(approval_id)
        if record.state is not ApprovalState.EXECUTING:
            raise ApprovalStateError(
                f"approval action cannot finish from {record.state.value}"
            )
        ensure_approval_transition(record.state, target)
        updated = replace(
            record,
            state=target,
            completed_at=now or utc_now(),
            result_reference=result_reference,
            failure_reason=failure_reason,
        )
        return await self._repository.save(updated, expected_version=record.version)

    async def _expire(self, record: ApprovalRecord) -> ApprovalRecord:
        if not self._is_expirable(record):
            return record
        ensure_approval_transition(record.state, ApprovalState.EXPIRED)
        expired = replace(
            record,
            state=ApprovalState.EXPIRED,
            completed_at=record.expires_at,
            failure_reason="approval expired",
        )
        return await self._repository.save(expired, expected_version=record.version)

    @staticmethod
    def _is_expirable(record: ApprovalRecord) -> bool:
        return record.state in {
            ApprovalState.WAITING_APPROVAL,
            ApprovalState.APPROVED,
        }

    @staticmethod
    def _validate_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("approval timestamps must be timezone-aware")
