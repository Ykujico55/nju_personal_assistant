"""PostgreSQL adapter for persistent model disclosure consents.

Storage holds metadata and digests only; raw field values never reach the
database.  Idempotent commands are journaled in ``model_disclosure_commands``
with the primary key ``(scope, idempotency_key)`` so concurrent connections
cannot create conflicting records.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import asyncpg

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

from ._support import aware_utc
from .connection import PostgresDatabase

_CONSENT_COLUMNS = (
    "id, owner_id, provider_id, purpose, field_digest, recipient_fingerprint, "
    "field_count, policy_version, state, created_at, expires_at, revoked_at, version"
)


def _row_to_record(row: asyncpg.Record) -> DisclosureConsentRecord:
    return DisclosureConsentRecord(
        id=row["id"],
        user_id=row["owner_id"],
        provider_id=row["provider_id"],
        purpose=row["purpose"],
        field_digest=row["field_digest"].strip(),
        recipient_fingerprint=row["recipient_fingerprint"].strip(),
        field_count=row["field_count"],
        policy_version=row["policy_version"],
        state=DisclosureConsentState(row["state"]),
        created_at=aware_utc(row["created_at"]),
        expires_at=aware_utc(row["expires_at"]),
        revoked_at=aware_utc(row["revoked_at"]),
        version=row["version"],
    )


class PostgresDisclosureConsentStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    async def create(
        self,
        record: DisclosureConsentRecord,
        *,
        command_scope: str,
        idempotency_key: str,
        command_fingerprint: str,
    ) -> DisclosureConsentRecord:
        for attempt in (0, 1):
            try:
                async with self._db.transaction(), self._db.connection() as connection:
                    command = await connection.fetchrow(
                        """
                        SELECT command_fingerprint, consent_id
                          FROM model_disclosure_commands
                         WHERE scope = $1 AND idempotency_key = $2
                        """,
                        command_scope,
                        idempotency_key,
                    )
                    if command is not None:
                        return await self._replay(connection, command, command_fingerprint)
                    await self._insert_consent(connection, record)
                    await connection.execute(
                        """
                        INSERT INTO model_disclosure_commands (
                            scope, idempotency_key, command_fingerprint, consent_id
                        ) VALUES ($1, $2, $3, $4)
                        """,
                        command_scope,
                        idempotency_key,
                        command_fingerprint,
                        record.id,
                    )
                    return record
            except asyncpg.UniqueViolationError as exc:
                # A concurrent connection committed the same command key first.
                # Retry once to read the winner and enforce fingerprint equality.
                if attempt == 1:
                    raise ConcurrentModificationError(
                        f"disclosure consent command {command_scope}/{idempotency_key} raced"
                    ) from exc
        raise ConcurrentModificationError("disclosure consent command did not settle")

    async def get(self, consent_id: str) -> DisclosureConsentRecord:
        async with self._db.connection() as connection:
            return await self._get(connection, consent_id)

    async def save(
        self, record: DisclosureConsentRecord, *, expected_version: int
    ) -> DisclosureConsentRecord:
        async with self._db.transaction(), self._db.connection() as connection:
            row = await connection.fetchrow(
                """
                UPDATE model_disclosure_consents SET
                    state = $2, revoked_at = $3, version = $4
                 WHERE id = $1 AND version = $5
                RETURNING version
                """,
                record.id,
                record.state.value,
                record.revoked_at,
                expected_version + 1,
                expected_version,
            )
            if row is None:
                exists = await connection.fetchval(
                    "SELECT 1 FROM model_disclosure_consents WHERE id = $1", record.id
                )
                if exists is None:
                    raise DisclosureConsentNotFoundError(
                        f"disclosure consent not found: {record.id}"
                    )
                raise ConcurrentModificationError(
                    f"disclosure consent {record.id} expected version "
                    f"{expected_version} was stale"
                )
            return replace(record, version=row["version"])

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
        for attempt in (0, 1):
            expired = False
            result: DisclosureConsentRecord | None = None
            try:
                async with self._db.transaction(), self._db.connection() as connection:
                    command = await connection.fetchrow(
                        """
                        SELECT command_fingerprint, consent_id
                          FROM model_disclosure_commands
                         WHERE scope = $1 AND idempotency_key = $2
                        """,
                        command_scope,
                        idempotency_key,
                    )
                    if command is not None:
                        return await self._replay(connection, command, command_fingerprint)
                    record = await self._get(connection, consent_id, for_update=True)
                    if record.user_id != user_id:
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
                            f"disclosure consent {consent_id} is "
                            f"{record.state.value}, not active"
                        )
                    if record.expires_at <= revoked_at:
                        # Expiry is a terminal state recorded under the row
                        # lock; the error is raised only after the EXPIRED
                        # transition commits.
                        expired_row = await connection.fetchrow(
                            """
                            UPDATE model_disclosure_consents
                               SET state = 'EXPIRED', version = $2
                             WHERE id = $1 AND version = $3
                            RETURNING version
                            """,
                            consent_id,
                            record.version + 1,
                            record.version,
                        )
                        if expired_row is None:
                            raise ConcurrentModificationError(
                                f"disclosure consent {consent_id} changed during revocation"
                            )
                        expired = True
                    else:
                        updated = replace(
                            record,
                            state=DisclosureConsentState.REVOKED,
                            revoked_at=revoked_at,
                            version=expected_version + 1,
                        )
                        updated_row = await connection.fetchrow(
                            """
                            UPDATE model_disclosure_consents
                               SET state = 'REVOKED', revoked_at = $2, version = $3
                             WHERE id = $1 AND version = $4
                            RETURNING version
                            """,
                            consent_id,
                            revoked_at,
                            updated.version,
                            expected_version,
                        )
                        if updated_row is None:
                            raise ConcurrentModificationError(
                                f"disclosure consent {consent_id} changed during revocation"
                            )
                        await connection.execute(
                            """
                            INSERT INTO model_disclosure_commands (
                                scope, idempotency_key, command_fingerprint, consent_id
                            ) VALUES ($1, $2, $3, $4)
                            """,
                            command_scope,
                            idempotency_key,
                            command_fingerprint,
                            consent_id,
                        )
                        result = updated
            except asyncpg.UniqueViolationError as exc:
                if attempt == 1:
                    raise ConcurrentModificationError(
                        f"disclosure consent command {command_scope}/{idempotency_key} raced"
                    ) from exc
                continue
            if expired:
                raise DisclosureStateError(f"disclosure consent {consent_id} is expired")
            if result is not None:
                return result
        raise ConcurrentModificationError("disclosure consent command did not settle")

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
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                f"""
                SELECT {_CONSENT_COLUMNS}
                  FROM model_disclosure_consents
                 WHERE id = $1 AND owner_id = $2 AND provider_id = $3 AND purpose = $4
                   AND field_digest = $5 AND recipient_fingerprint = $6
                   AND policy_version = $7
                   AND state = 'ACTIVE' AND expires_at > $8
                """,
                consent_id,
                user_id,
                provider_id,
                purpose,
                field_digest,
                recipient_fingerprint,
                policy_version,
                now,
            )
        if row is None:
            return None
        return _row_to_record(row)

    async def list_for_user(
        self, user_id: str, *, limit: int = 100
    ) -> tuple[DisclosureConsentRecord, ...]:
        if limit < 1:
            raise ValidationError("limit must be positive")
        async with self._db.connection() as connection:
            rows = await connection.fetch(
                f"""
                SELECT {_CONSENT_COLUMNS}
                  FROM model_disclosure_consents
                 WHERE owner_id = $1
                 ORDER BY created_at, id
                 LIMIT $2
                """,
                user_id,
                limit,
            )
        return tuple(_row_to_record(row) for row in rows)

    async def _insert_consent(
        self, connection: asyncpg.Connection, record: DisclosureConsentRecord
    ) -> None:
        try:
            await connection.execute(
                """
                INSERT INTO model_disclosure_consents (
                    id, owner_id, provider_id, purpose, field_digest,
                    recipient_fingerprint, field_count, policy_version, state,
                    created_at, expires_at, revoked_at, version
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                """,
                record.id,
                record.user_id,
                record.provider_id,
                record.purpose,
                record.field_digest,
                record.recipient_fingerprint,
                record.field_count,
                record.policy_version,
                record.state.value,
                record.created_at,
                record.expires_at,
                record.revoked_at,
                record.version,
            )
        except asyncpg.UniqueViolationError as exc:
            raise AlreadyExistsError(
                f"disclosure consent already exists: {record.id}"
            ) from exc

    async def _replay(
        self,
        connection: asyncpg.Connection,
        command: asyncpg.Record,
        command_fingerprint: str,
    ) -> DisclosureConsentRecord:
        stored = command["command_fingerprint"].strip()
        if stored != command_fingerprint:
            raise DisclosureIdempotencyConflictError(
                "the idempotency key was already used with different content"
            )
        return await self._get(connection, command["consent_id"])

    async def _get(
        self,
        connection: asyncpg.Connection,
        consent_id: str,
        *,
        for_update: bool = False,
    ) -> DisclosureConsentRecord:
        lock = " FOR UPDATE" if for_update else ""
        row = await connection.fetchrow(
            f"SELECT {_CONSENT_COLUMNS} FROM model_disclosure_consents WHERE id = $1{lock}",
            consent_id,
        )
        if row is None:
            raise DisclosureConsentNotFoundError(
                f"disclosure consent not found: {consent_id}"
            )
        return _row_to_record(row)
