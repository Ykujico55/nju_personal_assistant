"""Content-addressed artifact blobs; metadata durability belongs in PostgreSQL."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from personal_assistant.core.artifacts import ArtifactHandle
from personal_assistant.domain import Sensitivity


class LocalArtifactBlobStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._references: dict[str, set[str]] = {}

    async def put(
        self, data: bytes, *, media_type: str, sensitivity: Sensitivity
    ) -> ArtifactHandle:
        digest = hashlib.sha256(data).hexdigest()
        storage_key = f"sha256/{digest[:2]}/{digest}"
        handle_id = f"art_{uuid4().hex}"
        target = self._resolve_key(storage_key)
        async with self._lock:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
                temporary.write_bytes(data)
                os.replace(temporary, target)
            self._references.setdefault(storage_key, set()).add(handle_id)
        return ArtifactHandle(
            id=handle_id,
            content_hash=digest,
            media_type=media_type,
            size_bytes=len(data),
            sensitivity=sensitivity,
            storage_key=storage_key,
        )

    async def read(self, handle: ArtifactHandle) -> bytes:
        data = self._resolve_key(handle.storage_key).read_bytes()
        if len(data) != handle.size_bytes:
            raise OSError("artifact size verification failed")
        digest = hashlib.sha256(data).hexdigest()
        if digest != handle.content_hash:
            raise OSError("artifact content hash verification failed")
        return data

    async def delete(self, handle: ArtifactHandle) -> None:
        # Production reference counts live in PostgreSQL. This process-local map is
        # sufficient only for the explicitly development-only adapter.
        async with self._lock:
            references = self._references.get(handle.storage_key)
            if references is None:
                raise RuntimeError("artifact reference state is unavailable; refuse deletion")
            references.discard(handle.id)
            if references:
                return
            self._references.pop(handle.storage_key, None)
            self._resolve_key(handle.storage_key).unlink(missing_ok=True)

    def _resolve_key(self, storage_key: str) -> Path:
        candidate = (self._root / storage_key).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise PermissionError("artifact key escapes managed root")
        return candidate
