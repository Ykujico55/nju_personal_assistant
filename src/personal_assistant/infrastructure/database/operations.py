"""PostgreSQL persistence for extension operation state and diagnostics."""

from __future__ import annotations

from typing import Any

import asyncpg

from personal_assistant.core.extensions.errors import ExtensionOperationError
from personal_assistant.core.extensions.operations import (
    ExtensionOperation,
    OperationState,
    validate_diagnostic_code,
)

from .connection import PostgresDatabase

_COLUMNS = (
    "id, extension_id, operation, status, diagnostic_code, "
    "idempotency_key, command_fingerprint, request_scope, created_at, updated_at"
)


def _row_to_operation(row: Any) -> ExtensionOperation:
    return ExtensionOperation(
        id=row["id"],
        extension_id=row["extension_id"],
        operation=row["operation"],
        status=OperationState(row["status"]),
        diagnostic_code=row["diagnostic_code"],
        idempotency_key=row["idempotency_key"],
        command_fingerprint=row["command_fingerprint"],
        request_scope=row["request_scope"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresExtensionOperationStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    async def create(self, operation: ExtensionOperation) -> None:
        validate_diagnostic_code(operation.diagnostic_code)
        async with self._db.connection() as connection:
            existing = await connection.fetchval(
                "SELECT 1 FROM extension_operations WHERE id = $1", operation.id
            )
            if existing is not None:
                raise ExtensionOperationError(
                    "OPERATION_EXISTS", f"operation already exists: {operation.id}"
                )
            try:
                await connection.execute(
                    """
                    INSERT INTO extension_operations (
                        id, extension_id, operation, status, diagnostic_code,
                        idempotency_key, command_fingerprint, request_scope,
                        created_at, updated_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    operation.id,
                    operation.extension_id,
                    operation.operation,
                    operation.status.value,
                    operation.diagnostic_code,
                    operation.idempotency_key,
                    operation.command_fingerprint,
                    operation.request_scope,
                    operation.created_at,
                    operation.updated_at,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ExtensionOperationError(
                    "IDEMPOTENCY_CONFLICT",
                    "the idempotency key was already used for this command",
                ) from exc

    async def update(
        self,
        operation_id: str,
        *,
        status: OperationState,
        diagnostic_code: str | None = None,
    ) -> ExtensionOperation:
        validate_diagnostic_code(diagnostic_code)
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                f"""
                UPDATE extension_operations
                SET status = $2, diagnostic_code = $3, updated_at = now()
                WHERE id = $1
                RETURNING {_COLUMNS}
                """,
                operation_id,
                status.value,
                diagnostic_code,
            )
        if row is None:
            raise ExtensionOperationError(
                "OPERATION_NOT_FOUND", f"unknown operation: {operation_id}"
            )
        return _row_to_operation(row)

    async def get(self, operation_id: str) -> ExtensionOperation | None:
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_COLUMNS} FROM extension_operations WHERE id = $1",
                operation_id,
            )
        return _row_to_operation(row) if row is not None else None

    async def find_by_request_scope(
        self,
        request_scope: str,
        idempotency_key: str,
    ) -> ExtensionOperation | None:
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                f"""
                SELECT {_COLUMNS} FROM extension_operations
                WHERE request_scope = $1 AND idempotency_key = $2
                """,
                request_scope,
                idempotency_key,
            )
        return _row_to_operation(row) if row is not None else None

    async def interrupt_running(self, *, diagnostic_code: str) -> int:
        validate_diagnostic_code(diagnostic_code)
        async with self._db.connection() as connection:
            result = await connection.execute(
                """
                UPDATE extension_operations
                SET status = 'FAILED', diagnostic_code = $1, updated_at = now()
                WHERE status IN ('PENDING', 'RUNNING')
                """,
                diagnostic_code,
            )
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0


__all__ = ["PostgresExtensionOperationStore"]
