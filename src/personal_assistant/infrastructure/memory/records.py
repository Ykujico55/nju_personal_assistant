"""Test/development reference; production selects PostgreSQL."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from personal_assistant.core.records import Command, fingerprint
from personal_assistant.domain import ConcurrentModificationError, ValidationError


class InMemoryRecordStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], dict[str, Any]] = {}
        self._commands: dict[str, tuple[str, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._command_owner: asyncio.Task[Any] | None = None

    async def get(self, bucket: str, key: str) -> dict[str, Any] | None:
        async with self._lock:
            return deepcopy(self._records.get((bucket, key)))

    async def list(self, bucket: str) -> tuple[dict[str, Any], ...]:
        async with self._lock:
            return tuple(deepcopy(v) for (b, _), v in self._records.items() if b == bucket)

    async def put(
        self, bucket: str, key: str, value: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]:
        fingerprint(value)
        async with self._lock:
            old = self._records.get((bucket, key))
            if (old["version"] if old else 0) != expected_version:
                raise ConcurrentModificationError("record version changed")
            record = {"id": key, "version": expected_version + 1, "value": deepcopy(value)}
            self._records[(bucket, key)] = record
            return deepcopy(record)

    async def command(self, key: str, request: object, action: Command) -> dict[str, Any]:
        if not key or len(key) > 256:
            raise ValidationError("a bounded command key is required")
        digest = fingerprint(request)

        async def apply() -> dict[str, Any]:
            old = self._commands.get(key)
            if old is not None:
                if old[0] != digest:
                    raise ConcurrentModificationError("command key belongs to another request")
                return deepcopy(old[1])
            result = await action()
            self._commands[key] = (digest, deepcopy(result))
            return deepcopy(result)

        task = asyncio.current_task()
        if task is self._command_owner:
            return await apply()
        async with self._command_lock:
            self._command_owner = task
            try:
                return await apply()
            finally:
                self._command_owner = None
