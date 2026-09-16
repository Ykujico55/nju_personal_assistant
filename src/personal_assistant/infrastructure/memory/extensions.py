"""In-memory lifecycle store test double."""

from __future__ import annotations

import asyncio
from copy import deepcopy

from personal_assistant.core.extensions.models import ExtensionRecord


class InMemoryLifecycleStore:
    def __init__(self) -> None:
        self._records: dict[str, ExtensionRecord] = {}
        self._lock = asyncio.Lock()

    async def get(self, extension_id: str) -> ExtensionRecord | None:
        async with self._lock:
            record = self._records.get(extension_id)
            return deepcopy(record) if record is not None else None

    async def save(self, record: ExtensionRecord) -> None:
        async with self._lock:
            self._records[record.manifest.id] = deepcopy(record)
