"""Storage lifecycle boundary shared by the API, workers and tests."""

from __future__ import annotations

from typing import Protocol


class StorageLifecycle(Protocol):
    async def startup(self) -> object: ...

    async def close(self) -> None: ...


class NullStorageLifecycle:
    """No-op lifecycle for the in-memory development/test adapters."""

    async def startup(self) -> object:
        return ()

    async def close(self) -> None:
        return None
