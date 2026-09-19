"""PostgreSQL delivery ledger for the generic host mail capability.

Every transition is a single atomic statement with an explicit status guard, so
cross-connection races cannot insert duplicates, cannot regress a terminal
state, and cannot report success without a durable finalize.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from typing import Any

from personal_assistant.core.mail import (
    MailDeliveryRecord,
    MailDeliveryStatus,
    MailLedgerConflictError,
    MailLedgerStateError,
    MailRecipientResult,
    MailRecipientStatus,
)

from .connection import PostgresDatabase

_TERMINAL = (
    MailDeliveryStatus.SUCCEEDED,
    MailDeliveryStatus.PARTIAL,
    MailDeliveryStatus.FAILED,
    MailDeliveryStatus.UNKNOWN,
)

_PREPARE_SQL = (
    "INSERT INTO mail_delivery_actions ("
    "local_action_id, account_id, message_id, status, envelope_digest, "
    "mime_sha256, recipient_results) "
    "VALUES ($1, $2, $3, 'PREPARED', $4, $5, '[]'::jsonb) "
    "ON CONFLICT (local_action_id) DO UPDATE SET "
    "updated_at = mail_delivery_actions.updated_at "
    "WHERE mail_delivery_actions.envelope_digest = EXCLUDED.envelope_digest "
    "RETURNING status"
)

_BEGIN_SQL = (
    "UPDATE mail_delivery_actions SET status = 'EXECUTING', owner_id = $3, "
    "lease_expires_at = now() + make_interval(secs => $4), updated_at = now() "
    "WHERE local_action_id = $1 AND envelope_digest = $2 AND status = 'PREPARED' "
    "RETURNING status"
)

_RECOVER_SQL = (
    "UPDATE mail_delivery_actions SET status = 'UNKNOWN', "
    "diagnostic_code = 'EXECUTION_LEASE_EXPIRED', updated_at = now() "
    "WHERE status = 'EXECUTING' AND lease_expires_at IS NOT NULL "
    "AND lease_expires_at <= now() "
    "AND (owner_id IS NULL OR NOT (owner_id = ANY($1::text[]))) "
    "RETURNING local_action_id"
)

_HEARTBEAT_SQL = (
    "UPDATE mail_delivery_actions SET "
    "lease_expires_at = now() + make_interval(secs => $3), updated_at = now() "
    "WHERE local_action_id = $1 AND owner_id = $2 AND status = 'EXECUTING' "
    "RETURNING local_action_id"
)

_FINALIZE_SQL = (
    "UPDATE mail_delivery_actions SET status = $3, recipient_results = $4::jsonb, "
    "server_code = $5, diagnostic_code = $6, updated_at = now() "
    "WHERE local_action_id = $1 AND envelope_digest = $2 AND status = 'EXECUTING' "
    "RETURNING status"
)

_RECONCILE_SQL = (
    "UPDATE mail_delivery_actions SET status = $4, "
    "server_code = COALESCE(server_code, 'SENT_RECONCILED'), "
    "diagnostic_code = $5, updated_at = now() "
    "WHERE local_action_id = $1 AND account_id = $2 AND message_id = $3 "
    "AND status = 'UNKNOWN' "
    "RETURNING status"
)


class PostgresMailDeliveryLedger:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def prepare(self, record: MailDeliveryRecord) -> MailDeliveryStatus:
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                _PREPARE_SQL,
                record.local_action_id,
                record.account_id,
                record.message_id,
                record.envelope_digest,
                record.mime_sha256,
            )
            if row is not None:
                return MailDeliveryStatus(str(row["status"]))
            existing = await connection.fetchrow(
                "SELECT envelope_digest, status FROM mail_delivery_actions "
                "WHERE local_action_id = $1",
                record.local_action_id,
            )
        if existing is None:
            raise MailLedgerStateError("the ledger row disappeared during prepare")
        if existing["envelope_digest"] != record.envelope_digest:
            raise MailLedgerConflictError(
                "the local action id is already bound to a different envelope"
            )
        return MailDeliveryStatus(str(existing["status"]))

    async def begin_execution(
        self,
        local_action_id: str,
        envelope_digest: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> MailDeliveryStatus:
        if not owner_id or not 1 <= lease_seconds <= 86_400:
            raise MailLedgerStateError("invalid execution lease")
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                _BEGIN_SQL, local_action_id, envelope_digest, owner_id, lease_seconds
            )
            if row is not None:
                return MailDeliveryStatus(str(row["status"]))
            existing = await connection.fetchrow(
                "SELECT envelope_digest, status FROM mail_delivery_actions "
                "WHERE local_action_id = $1",
                local_action_id,
            )
        if existing is None:
            raise MailLedgerStateError("unknown local action id")
        if existing["envelope_digest"] != envelope_digest:
            raise MailLedgerConflictError(
                "the local action id is already bound to a different envelope"
            )
        # A live EXECUTING row (or any terminal state) is returned unchanged:
        # the second caller must never burn the first caller's dispatch.
        return MailDeliveryStatus(str(existing["status"]))

    async def finalize(
        self,
        local_action_id: str,
        envelope_digest: str,
        *,
        status: MailDeliveryStatus,
        recipient_results: tuple[MailRecipientResult, ...] = (),
        server_code: str | None = None,
        diagnostic_code: str | None = None,
    ) -> MailDeliveryStatus:
        if status not in _TERMINAL:
            raise MailLedgerStateError("finalize requires a terminal status")
        payload = _results_payload(recipient_results)
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                _FINALIZE_SQL,
                local_action_id,
                envelope_digest,
                status.value,
                payload,
                server_code,
                diagnostic_code,
            )
            if row is not None:
                return MailDeliveryStatus(str(row["status"]))
            existing = await connection.fetchrow(
                "SELECT envelope_digest, status FROM mail_delivery_actions "
                "WHERE local_action_id = $1",
                local_action_id,
            )
        if existing is None:
            raise MailLedgerStateError("unknown local action id")
        if existing["envelope_digest"] != envelope_digest:
            raise MailLedgerConflictError(
                "the local action id is already bound to a different envelope"
            )
        current = MailDeliveryStatus(str(existing["status"]))
        if current.terminal:
            # Idempotent duplicate finalize; terminal states never move back.
            return current
        raise MailLedgerStateError("the ledger row is not EXECUTING")

    async def reconcile(
        self,
        local_action_id: str,
        *,
        account_id: str,
        message_id: str,
        status: MailDeliveryStatus,
        diagnostic_code: str | None = None,
    ) -> MailDeliveryStatus:
        if status not in _TERMINAL:
            raise MailLedgerStateError("reconciliation requires a terminal status")
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                _RECONCILE_SQL,
                local_action_id,
                account_id,
                message_id,
                status.value,
                diagnostic_code,
            )
            if row is not None:
                return MailDeliveryStatus(str(row["status"]))
            existing = await connection.fetchrow(
                "SELECT status FROM mail_delivery_actions WHERE local_action_id = $1",
                local_action_id,
            )
        if existing is None:
            raise MailLedgerStateError("unknown local action id")
        return MailDeliveryStatus(str(existing["status"]))

    async def heartbeat(
        self, local_action_id: str, owner_id: str, *, lease_seconds: float
    ) -> bool:
        if not owner_id or not 1 <= lease_seconds <= 86_400:
            raise MailLedgerStateError("invalid execution lease")
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                _HEARTBEAT_SQL, local_action_id, owner_id, lease_seconds
            )
        return row is not None

    async def recover_stale_executions(
        self, *, active_owners: Collection[str] = ()
    ) -> tuple[str, ...]:
        async with self._database.connection() as connection:
            rows = await connection.fetch(_RECOVER_SQL, list(active_owners))
        return tuple(str(row["local_action_id"]) for row in rows)

    async def get(self, local_action_id: str) -> MailDeliveryRecord | None:
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                "SELECT local_action_id, account_id, message_id, status, envelope_digest, "
                "mime_sha256, recipient_results, server_code, diagnostic_code, "
                "owner_id, lease_expires_at "
                "FROM mail_delivery_actions WHERE local_action_id = $1",
                local_action_id,
            )
        if row is None:
            return None
        return _to_record(dict(row))

    async def close(self) -> None:
        return None


def _results_payload(results: tuple[MailRecipientResult, ...]) -> str:
    return json.dumps(
        [
            {
                "recipient": item.recipient,
                "status": item.status.value,
                "error_code": item.error_code,
            }
            for item in results
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _to_record(row: Mapping[str, Any]) -> MailDeliveryRecord:
    results_raw = row.get("recipient_results")
    if isinstance(results_raw, str):
        results_raw = json.loads(results_raw)
    results: list[MailRecipientResult] = []
    if isinstance(results_raw, list):
        for item in results_raw:
            if not isinstance(item, Mapping):
                continue
            try:
                results.append(
                    MailRecipientResult(
                        recipient=str(item["recipient"]),
                        status=MailRecipientStatus(str(item["status"])),
                        error_code=(
                            str(item["error_code"])
                            if item.get("error_code") is not None
                            else None
                        ),
                    )
                )
            except (KeyError, ValueError):
                continue
    return MailDeliveryRecord(
        local_action_id=str(row["local_action_id"]),
        account_id=str(row["account_id"]),
        message_id=str(row["message_id"]),
        status=MailDeliveryStatus(str(row["status"])),
        envelope_digest=str(row["envelope_digest"]),
        mime_sha256=str(row["mime_sha256"]),
        recipient_results=tuple(results),
        server_code=str(row["server_code"]) if row.get("server_code") else None,
        diagnostic_code=(
            str(row["diagnostic_code"]) if row.get("diagnostic_code") else None
        ),
        owner_id=str(row["owner_id"]) if row.get("owner_id") else None,
        lease_expires_at=row.get("lease_expires_at"),
    )


__all__ = ["PostgresMailDeliveryLedger"]
