"""Test-only secret store. Production must use the OS credential store."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from personal_assistant.core.secrets import SecretHandle


class InMemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def put(self, *, name: str, kind: str, value: str) -> SecretHandle:
        del name
        if not value:
            raise ValueError("secret value may not be empty")
        handle = SecretHandle(id=uuid4().hex, kind=kind)
        async with self._lock:
            self._values[handle.id] = value
        return handle

    async def resolve_for_broker(self, handle: SecretHandle, *, purpose: str) -> str:
        if not purpose.strip():
            raise ValueError("purpose is required")
        async with self._lock:
            try:
                return self._values[handle.id]
            except KeyError as exc:
                raise KeyError("unknown secret handle") from exc

    async def delete(self, handle: SecretHandle) -> None:
        async with self._lock:
            self._values.pop(handle.id, None)

