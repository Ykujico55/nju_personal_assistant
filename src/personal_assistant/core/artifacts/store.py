from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from personal_assistant.domain import Sensitivity


@dataclass(frozen=True, slots=True)
class ArtifactHandle:
    id: str
    content_hash: str
    media_type: str
    size_bytes: int
    sensitivity: Sensitivity
    storage_key: str


class ArtifactStorePort(Protocol):
    async def put(
        self, data: bytes, *, media_type: str, sensitivity: Sensitivity
    ) -> ArtifactHandle: ...

    async def read(self, handle: ArtifactHandle) -> bytes: ...

    async def delete(self, handle: ArtifactHandle) -> None: ...

