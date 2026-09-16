"""In-memory extension operation store (development and tests only)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from personal_assistant.core.extensions.errors import ExtensionOperationError
from personal_assistant.core.extensions.operations import (
    TERMINAL_OPERATION_STATES,
    ExtensionOperation,
    OperationState,
    validate_diagnostic_code,
)


class InMemoryExtensionOperationStore:
    def __init__(self) -> None:
        self._operations: dict[str, ExtensionOperation] = {}
        self._lock = asyncio.Lock()

    async def create(self, operation: ExtensionOperation) -> None:
        validate_diagnostic_code(operation.diagnostic_code)
        async with self._lock:
            if operation.id in self._operations:
                raise ExtensionOperationError(
                    "OPERATION_EXISTS", f"operation already exists: {operation.id}"
                )
            if operation.idempotency_key is not None and any(
                (
                    existing.request_scope == operation.request_scope
                    or (
                        existing.extension_id == operation.extension_id
                        and existing.operation == operation.operation
                    )
                )
                and existing.idempotency_key == operation.idempotency_key
                for existing in self._operations.values()
            ):
                raise ExtensionOperationError(
                    "IDEMPOTENCY_CONFLICT",
                    "the idempotency key was already used for this command",
                )
            self._operations[operation.id] = operation

    async def update(
        self,
        operation_id: str,
        *,
        status: OperationState,
        diagnostic_code: str | None = None,
    ) -> ExtensionOperation:
        validate_diagnostic_code(diagnostic_code)
        async with self._lock:
            existing = self._operations.get(operation_id)
            if existing is None:
                raise ExtensionOperationError(
                    "OPERATION_NOT_FOUND", f"unknown operation: {operation_id}"
                )
            updated = replace(
                existing,
                status=status,
                diagnostic_code=diagnostic_code,
                updated_at=datetime.now(UTC),
            )
            self._operations[operation_id] = updated
            return updated

    async def get(self, operation_id: str) -> ExtensionOperation | None:
        async with self._lock:
            return self._operations.get(operation_id)

    async def find_by_request_scope(
        self,
        request_scope: str,
        idempotency_key: str,
    ) -> ExtensionOperation | None:
        async with self._lock:
            for existing in self._operations.values():
                if (
                    existing.request_scope == request_scope
                    and existing.idempotency_key == idempotency_key
                ):
                    return existing
            return None

    async def interrupt_running(self, *, diagnostic_code: str) -> int:
        validate_diagnostic_code(diagnostic_code)
        async with self._lock:
            interrupted = 0
            for operation_id, operation in list(self._operations.items()):
                if operation.status in TERMINAL_OPERATION_STATES:
                    continue
                self._operations[operation_id] = replace(
                    operation,
                    status=OperationState.FAILED,
                    diagnostic_code=diagnostic_code,
                    updated_at=datetime.now(UTC),
                )
                interrupted += 1
            return interrupted


__all__ = ["InMemoryExtensionOperationStore"]
