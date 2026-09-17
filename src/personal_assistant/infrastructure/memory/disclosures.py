"""In-memory disclosure-consent store for tests and development only.

Mirrors the PostgreSQL adapter's idempotency, CAS and revocation semantics.
It is selected only in development/test; production configuration refuses it.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import datetime

from personal_assistant.core.models.disclosure import (
    DisclosureConsentNotFoundError,
    DisclosureConsentRecord,
    DisclosureConsentState,
    DisclosureIdempotencyConflictError,
    DisclosureStateError,
)
from personal_assistant.domain import (
    AlreadyExistsError,
    ConcurrentModificationError,
    ValidationError,
)


class InMemoryDisclosureConsentStore:
    def __init__(self) -> None:
        self._records: dict[str, DisclosureConsentRecord] = {}
        self._commands: dict[tuple[str, str], tuple[str, str]] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        record: DisclosureConsentRecord,
        *,
        command_scope: str,
        idempotency_key: str,
        command_fingerprint: str,
    ) -> DisclosureConsentRecord:
        async with self._lock:
            command_key = (command_scope, idempotency_key)
            existing = self._commands.get(command_key)
            if existing is not None:
                fingerprint, consent_id = existing
                if fingerprint != command_fingerprint:
                    raise DisclosureIdempotencyConflictError(
                        "the idempotency key was already used with different content"
                    )
                return copy.deepcopy(self._records[consent_id])
            if record.id in self._records:
                raise AlreadyExistsError(f"disclosure consent already exists: {record.id}")
            self._records[record.id] = copy.deepcopy(record)
            self._commands[command_key] = (command_fingerprint, record.id)
            return copy.deepcopy(record)

    async def get(self, consent_id: str) -> DisclosureConsentRecord:
        async with self._lock:
            try:
                return copy.deepcopy(self._records[consent_id])
            except KeyError as exc:
                raise DisclosureConsentNotFoundError(
                    f"disclosure consent not found: {consent_id}"
                ) from exc

    async def save(
        self, record: DisclosureConsentRecord, *, expected_version: int
    ) -> DisclosureConsentRecord:
        async with self._lock:
            current = self._records.get(record.id)
            if current is None:
                raise DisclosureConsentNotFoundError(
                    f"disclosure consent not found: {record.id}"
                )
            if current.version != expected_version:
                raise ConcurrentModificationError(
                    f"disclosure consent {record.id} expected version "
                    f"{expected_version}, found {current.version}"
                )
            saved = replace(record, version=expected_version + 1)
            self._records[record.id] = copy.deepcopy(saved)
            return copy.deepcopy(saved)

    async def revoke(
        self,
        consent_id: str,
        *,
        user_id: str,
        expected_version: int,
        revoked_at: datetime,
        command_scope: str,
        idempotency_key: str,
        command_fingerprint: str,
    ) -> DisclosureConsentRecord:
        async with self._lock:
            command_key = (command_scope, idempotency_key)
            existing = self._commands.get(command_key)
            if existing is not None:
                fingerprint, existing_id = existing
                if fingerprint != command_fingerprint:
                    raise DisclosureIdempotencyConflictError(
                        "the idempotency key was already used with different content"
                    )
                return copy.deepcopy(self._records[existing_id])
            record = self._records.get(consent_id)
            if record is None or record.user_id != user_id:
                raise DisclosureConsentNotFoundError(
                    f"disclosure consent not found: {consent_id}"
                )
            if record.version != expected_version:
                raise ConcurrentModificationError(
                    f"disclosure consent {consent_id} expected version "
                    f"{expected_version}, found {record.version}"
                )
            if record.state is not DisclosureConsentState.ACTIVE:
                raise DisclosureStateError(
                    f"disclosure consent {consent_id} is {record.state.value}, not active"
                )
            if record.expires_at <= revoked_at:
                # Expiry is a terminal state: record it and refuse to revoke,
                # matching the PostgreSQL adapter.
                expired = replace(
                    record,
                    state=DisclosureConsentState.EXPIRED,
                    version=record.version + 1,
                )
                self._records[consent_id] = copy.deepcopy(expired)
                raise DisclosureStateError(
                    f"disclosure consent {consent_id} is expired"
                )
            updated = replace(
                record,
                state=DisclosureConsentState.REVOKED,
                revoked_at=revoked_at,
                version=record.version + 1,
            )
            self._records[consent_id] = copy.deepcopy(updated)
            self._commands[command_key] = (command_fingerprint, consent_id)
            return copy.deepcopy(updated)

    async def find_active(
        self,
        *,
        consent_id: str,
        user_id: str,
        provider_id: str,
        purpose: str,
        field_digest: str,
        recipient_fingerprint: str,
        policy_version: str,
        now: datetime,
    ) -> DisclosureConsentRecord | None:
        async with self._lock:
            record = self._records.get(consent_id)
        if record is None:
            return None
        if (
            record.user_id == user_id
            and record.provider_id == provider_id
            and record.purpose == purpose
            and record.field_digest == field_digest
            and record.recipient_fingerprint == recipient_fingerprint
            and record.policy_version == policy_version
            and record.state is DisclosureConsentState.ACTIVE
            and record.expires_at > now
        ):
            return copy.deepcopy(record)
        return None

    async def list_for_user(
        self, user_id: str, *, limit: int = 100
    ) -> tuple[DisclosureConsentRecord, ...]:
        if limit < 1:
            raise ValidationError("limit must be positive")
        async with self._lock:
            records = [
                copy.deepcopy(record)
                for record in self._records.values()
                if record.user_id == user_id
            ]
        records.sort(key=lambda item: (item.created_at, item.id))
        return tuple(records[:limit])
