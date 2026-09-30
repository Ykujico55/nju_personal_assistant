"""In-memory lifecycle store test double."""

from __future__ import annotations

import asyncio
from copy import deepcopy

from personal_assistant.core.extensions.models import ExtensionRecord, ExtensionState


class InMemoryLifecycleStore:
    def __init__(self) -> None:
        self._records: dict[str, ExtensionRecord] = {}
        self._lock = asyncio.Lock()

    async def get(self, extension_id: str) -> ExtensionRecord | None:
        async with self._lock:
            record = self._records.get(extension_id)
            return deepcopy(record) if record is not None else None

    async def all(self) -> tuple[ExtensionRecord, ...]:
        async with self._lock:
            return tuple(
                deepcopy(self._records[key]) for key in sorted(self._records)
            )

    async def save(
        self,
        record: ExtensionRecord,
        *,
        expected: tuple[ExtensionState, str] | None = None,
    ) -> bool:
        async with self._lock:
            current = self._records.get(record.manifest.id)
            if expected is not None and (
                current is None
                or (current.state, current.manifest.version) != expected
            ):
                return False
            self._records[record.manifest.id] = deepcopy(record)
            return True
