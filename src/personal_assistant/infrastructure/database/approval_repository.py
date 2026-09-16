"""PostgreSQL approval repository with compare-and-swap versioning."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import asyncpg

from personal_assistant.core.approvals.canonicalize import canonical_json, canonical_sha256
from personal_assistant.core.approvals.service import ApprovalRecord
from personal_assistant.domain import (
    AlreadyExistsError,
    ConcurrentModificationError,
    NotFoundError,
)
from personal_assistant.domain.enums import ApprovalState

from ._support import aware_utc
from .connection import PostgresDatabase

_APPROVAL_COLUMNS = (
    "id, status, action_fingerprint, canonical_action, nonce, created_at, expires_at, "
    "version, approved_at, approved_by, consumed_at, completed_at, result_reference, "
    "failure_reason"
)


def _derive_columns(action: dict[str, Any], record: ApprovalRecord) -> dict[str, Any]:
    attachments = action.get("attachments")
    attachment_hashes: list[str] = []
    if isinstance(attachments, list):
        for item in attachments:
            if isinstance(item, dict) and item.get("sha256") is not None:
                attachment_hashes.append(str(item["sha256"]))
    return {
        "action_id": str(action.get("action_type") or action.get("tool_id") or record.id),
        "task_id": action.get("task_id"),
        "target": action.get("target"),
        "canonical_payload_sha256": canonical_sha256(action.get("payload")),
        "attachment_sha256": attachment_hashes,
        "extension_id": str(action.get("extension_id") or "unknown"),
        "extension_version": str(action.get("extension_version") or "0"),
    }


def _row_to_record(row: asyncpg.Record) -> ApprovalRecord:
    raw_action = row["canonical_action"]
    if isinstance(raw_action, str):
        raw_action = json.loads(raw_action)
    return ApprovalRecord(
        id=row["id"],
        state=ApprovalState(row["status"]),
        action_fingerprint=row["action_fingerprint"].strip(),
        canonical_action=canonical_json(raw_action),
        nonce=row["nonce"],
        created_at=aware_utc(row["created_at"]),
        expires_at=aware_utc(row["expires_at"]),
        version=row["version"],
        approved_at=aware_utc(row["approved_at"]),
        approved_by=row["approved_by"],
        consumed_at=aware_utc(row["consumed_at"]),
        completed_at=aware_utc(row["completed_at"]),
        result_reference=row["result_reference"],
        failure_reason=row["failure_reason"],
    )


class PostgresApprovalRepository:
    def __init__(self, database: PostgresDatabase, *, owner_id: str = "owner") -> None:
        self._db = database
        self._owner_id = owner_id

    async def create(self, record: ApprovalRecord) -> None:
        action = json.loads(record.canonical_action)
        derived = _derive_columns(action, record)
        async with self._db.transaction(), self._db.connection() as connection:
            try:
                await connection.execute(
                    """
                    INSERT INTO approvals (
                        id, owner_id, task_id, action_id, target, canonical_action,
                        action_fingerprint, canonical_payload_sha256, attachment_sha256,
                        extension_id, extension_version, nonce, status, expires_at,
                        consumed_at, created_at, version, approved_at, approved_by,
                        completed_at, result_reference, failure_reason
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                        $15, $16, $17, $18, $19, $20, $21, $22
                    )
                    """,
                    record.id,
                    self._owner_id,
                    derived["task_id"],
                    derived["action_id"],
                    derived["target"],
                    action,
                    record.action_fingerprint,
                    derived["canonical_payload_sha256"],
                    derived["attachment_sha256"],
                    derived["extension_id"],
                    derived["extension_version"],
                    record.nonce,
                    record.state.value,
                    record.expires_at,
                    record.consumed_at,
                    record.created_at,
                    record.version,
                    record.approved_at,
                    record.approved_by,
                    record.completed_at,
                    record.result_reference,
                    record.failure_reason,
                )
            except asyncpg.UniqueViolationError as exc:
                raise AlreadyExistsError(f"approval already exists: {record.id}") from exc
            except asyncpg.ForeignKeyViolationError as exc:
                raise NotFoundError(
                    f"task not found for approval: {derived['task_id']}"
                ) from exc

    async def get(self, approval_id: str) -> ApprovalRecord:
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_APPROVAL_COLUMNS} FROM approvals WHERE id = $1", approval_id
            )
        if row is None:
            raise NotFoundError(f"approval not found: {approval_id}")
        return _row_to_record(row)

    async def save(
        self, record: ApprovalRecord, *, expected_version: int
    ) -> ApprovalRecord:
        async with self._db.transaction(), self._db.connection() as connection:
            row = await connection.fetchrow(
                """
                UPDATE approvals SET
                    status = $2, version = $3, approved_at = $4, approved_by = $5,
                    consumed_at = $6, completed_at = $7, result_reference = $8,
                    failure_reason = $9
                 WHERE id = $1 AND version = $10
                RETURNING version
                """,
                record.id,
                record.state.value,
                expected_version + 1,
                record.approved_at,
                record.approved_by,
                record.consumed_at,
                record.completed_at,
                record.result_reference,
                record.failure_reason,
                expected_version,
            )
            if row is None:
                exists = await connection.fetchval(
                    "SELECT 1 FROM approvals WHERE id = $1", record.id
                )
                if exists is None:
                    raise NotFoundError(f"approval not found: {record.id}")
                raise ConcurrentModificationError(
                    f"approval {record.id} expected version {expected_version} was stale"
                )
            return replace(record, version=row["version"])
