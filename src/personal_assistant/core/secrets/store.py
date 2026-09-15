from __future__ import annotations

from typing import Protocol

from .handles import SecretHandle


class SecretStorePort(Protocol):
    async def put(self, *, name: str, kind: str, value: str) -> SecretHandle: ...

    async def resolve_for_broker(self, handle: SecretHandle, *, purpose: str) -> str: ...

    async def delete(self, handle: SecretHandle) -> None: ...

