"""In-memory test doubles for the supervised browser stores.

Same observable state machine as the PostgreSQL adapters, but not crash-safe:
these exist for unit tests and the memory development backend only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from personal_assistant.core.browser import (
    BrowserAdapterRecord,
    BrowserSessionRecord,
    BrowserSessionState,
)
from personal_assistant.domain.errors import (
    AlreadyExistsError,
    ConcurrentModificationError,
    NotFoundError,
)

# UNKNOWN is a pending external action: it occupies the task slot until a
# read-only reconciliation resolves it (mirrors the 0008 partial unique index).
_OPEN_STATES = frozenset(
    state
    for state in BrowserSessionState
    if state
    not in (
        BrowserSessionState.SUCCEEDED,
        BrowserSessionState.FAILED,
        BrowserSessionState.CANCELLED,
    )
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InMemoryBrowserSessionStore:
    def __init__(self) -> None:
        self._records: dict[str, BrowserSessionRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: BrowserSessionRecord) -> BrowserSessionRecord:
        async with self._lock:
            if record.session_id in self._records:
                raise AlreadyExistsError(f"browser session {record.session_id} already exists")
            if record.state in _OPEN_STATES and any(
                item.task_id == record.task_id and item.state in _OPEN_STATES
                for item in self._records.values()
            ):
                raise AlreadyExistsError(
                    f"an open browser session already exists for task {record.task_id}"
                )
            stored = replace(record, version=0)
            self._records[record.session_id] = stored
            return stored

    async def get(self, session_id: str) -> BrowserSessionRecord | None:
        async with self._lock:
            record = self._records.get(session_id)
            return None if record is None else replace(record)

    async def save(
        self, record: BrowserSessionRecord, *, expected_version: int
    ) -> BrowserSessionRecord:
        async with self._lock:
            current = self._records.get(record.session_id)
            if current is None:
                raise NotFoundError(f"browser session {record.session_id} not found")
            if current.version != expected_version:
                raise ConcurrentModificationError(
                    "the browser session was modified by another writer"
                )
            stored = replace(record, version=expected_version + 1, updated_at=_utcnow())
            self._records[record.session_id] = stored
            return stored

    async def find_active_for_task(self, task_id: str) -> tuple[BrowserSessionRecord, ...]:
        async with self._lock:
            records = [
                replace(record)
                for record in self._records.values()
                if record.task_id == task_id
            ]
        records.sort(key=lambda item: item.created_at)
        return tuple(records)

    async def recover_stale_executions(
        self, *, active_owners: Sequence[str] = ()
    ) -> tuple[str, ...]:
        active = set(active_owners)
        recovered: list[str] = []
        async with self._lock:
            for session_id, record in list(self._records.items()):
                if record.state is not BrowserSessionState.EXECUTING:
                    continue
                if record.owner_id and record.owner_id in active:
                    continue
                self._records[session_id] = replace(
                    record,
                    state=BrowserSessionState.UNKNOWN,
                    outcome="UNKNOWN",
                    diagnostic_code="EXECUTION_INTERRUPTED",
                    updated_at=_utcnow(),
                    version=record.version + 1,
                )
                recovered.append(session_id)
        return tuple(recovered)

    async def close(self) -> None:
        return None


class InMemoryBrowserAdapterStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], BrowserAdapterRecord] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, record: BrowserAdapterRecord) -> BrowserAdapterRecord:
        key = (record.extension_id, record.adapter_id, record.adapter_version)
        async with self._lock:
            current = self._records.get(key)
            version = current.version + 1 if current else 0
            stored = replace(record, version=version, updated_at=_utcnow())
            self._records[key] = stored
            return stored

    async def get(
        self, extension_id: str, adapter_id: str, adapter_version: str
    ) -> BrowserAdapterRecord | None:
        async with self._lock:
            record = self._records.get((extension_id, adapter_id, adapter_version))
            return None if record is None else replace(record)

    async def list_for_extension(self, extension_id: str) -> tuple[BrowserAdapterRecord, ...]:
        async with self._lock:
            records = [
                replace(record)
                for key, record in self._records.items()
                if key[0] == extension_id
            ]
        records.sort(key=lambda item: (item.adapter_id, item.adapter_version))
        return tuple(records)

    async def close(self) -> None:
        return None


__all__ = ["InMemoryBrowserAdapterStore", "InMemoryBrowserSessionStore"]
