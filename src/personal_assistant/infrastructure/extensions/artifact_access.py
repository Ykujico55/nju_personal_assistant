"""Host-owned artifact capability with persisted per-extension ownership.

Blobs are content-addressed by the host artifact store; a small metadata file per
handle records the owning extension version.  Reading or deleting an artifact
created by another extension fails closed.  Credentials never enter this store.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from personal_assistant.core.artifacts import ArtifactHandle, ArtifactStorePort
from personal_assistant.core.extensions.artifact_access import (
    CAPABILITY_ARTIFACT_READ,
    CAPABILITY_ARTIFACT_WRITE,
    HOST_ARTIFACT_METHODS,
    HOST_ARTIFACT_PUT,
    HOST_ARTIFACT_READ,
    ArtifactAccessError,
    ExtensionArtifactContext,
    decode_artifact_data,
    normalize_media_type,
)
from personal_assistant.core.extensions.artifact_access import (
    artifact_id as parse_artifact_id,
)
from personal_assistant.domain import Sensitivity

_MAX_METADATA_BYTES = 16 * 1024
_SENSITIVITIES = {item.value for item in Sensitivity}


class FileExtensionArtifactAccess:
    def __init__(self, store: ArtifactStorePort, metadata_root: Path) -> None:
        self._store = store
        self._root = metadata_root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return True

    async def handle(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        context: ExtensionArtifactContext,
    ) -> Any:
        if method not in HOST_ARTIFACT_METHODS:
            raise ArtifactAccessError(
                "ARTIFACT_PROTOCOL_ERROR", f"unknown artifact method: {method}"
            )
        if method == HOST_ARTIFACT_PUT:
            return await self._put(params, context)
        if method == HOST_ARTIFACT_READ:
            return await self._read(params, context, enforce_owner=True)
        return self._delete(params, context)

    async def read_as_host(self, artifact_id: str) -> bytes:
        """Host-internal read used by the mail executor; no ownership check."""

        metadata = self._load_metadata(artifact_id)
        handle = _handle_from_metadata(artifact_id, metadata)
        try:
            return await self._store.read(handle)
        except OSError as exc:
            raise ArtifactAccessError(
                "ARTIFACT_NOT_FOUND", "the artifact bytes could not be read"
            ) from exc

    async def metadata_for(self, artifact_id: str) -> Mapping[str, Any]:
        return self._load_metadata(artifact_id)

    async def aclose(self) -> None:
        return None

    async def _put(
        self, params: Mapping[str, Any], context: ExtensionArtifactContext
    ) -> Mapping[str, Any]:
        data = decode_artifact_data(params)
        media_type = normalize_media_type(params.get("media_type"))
        sensitivity_raw = params.get("sensitivity", Sensitivity.PERSONAL.value)
        if not isinstance(sensitivity_raw, str) or sensitivity_raw not in _SENSITIVITIES:
            raise ArtifactAccessError("ARTIFACT_INVALID", "artifact sensitivity is invalid")
        sensitivity = Sensitivity(sensitivity_raw)
        handle = await self._store.put(
            data, media_type=media_type, sensitivity=sensitivity
        )
        metadata = {
            "id": handle.id,
            "owner_extension_id": context.extension_id,
            "owner_extension_version": context.extension_version,
            "content_hash": handle.content_hash,
            "media_type": handle.media_type,
            "size_bytes": handle.size_bytes,
            "sensitivity": handle.sensitivity.value,
            "storage_key": handle.storage_key,
        }
        async with self._lock:
            self._write_metadata(handle.id, metadata)
        return dict(metadata)

    async def _read(
        self,
        params: Mapping[str, Any],
        context: ExtensionArtifactContext,
        *,
        enforce_owner: bool,
    ) -> Mapping[str, Any]:
        import base64

        identifier = parse_artifact_id(params)
        metadata = self._load_metadata(identifier)
        if enforce_owner and metadata.get("owner_extension_id") != context.extension_id:
            raise ArtifactAccessError(
                "ARTIFACT_FORBIDDEN", "this artifact belongs to another extension"
            )
        handle = _handle_from_metadata(identifier, metadata)
        try:
            data = await self._store.read(handle)
        except OSError as exc:
            raise ArtifactAccessError(
                "ARTIFACT_NOT_FOUND", "the artifact bytes could not be read"
            ) from exc
        return {
            "id": identifier,
            "content_hash": metadata["content_hash"],
            "media_type": metadata["media_type"],
            "size_bytes": metadata["size_bytes"],
            "sensitivity": metadata["sensitivity"],
            "data_base64": base64.b64encode(data).decode("ascii"),
        }

    async def _delete(
        self, params: Mapping[str, Any], context: ExtensionArtifactContext
    ) -> Mapping[str, Any]:
        identifier = parse_artifact_id(params)
        metadata = self._load_metadata(identifier)
        if metadata.get("owner_extension_id") != context.extension_id:
            raise ArtifactAccessError(
                "ARTIFACT_FORBIDDEN", "this artifact belongs to another extension"
            )
        handle = _handle_from_metadata(identifier, metadata)
        try:
            await self._store.delete(handle)
        except (OSError, RuntimeError) as exc:
            raise ArtifactAccessError(
                "ARTIFACT_NOT_FOUND", "the artifact could not be deleted"
            ) from exc
        async with self._lock:
            self._metadata_path(identifier).unlink(missing_ok=True)
        return {"deleted": identifier}

    def _load_metadata(self, identifier: str) -> Mapping[str, Any]:
        path = self._metadata_path(identifier)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ArtifactAccessError(
                "ARTIFACT_NOT_FOUND", "the artifact is unknown"
            ) from exc
        if len(raw) > _MAX_METADATA_BYTES:
            raise ArtifactAccessError("ARTIFACT_INVALID", "artifact metadata is invalid")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ArtifactAccessError("ARTIFACT_INVALID", "artifact metadata is invalid") from exc
        if not isinstance(data, dict) or data.get("id") != identifier:
            raise ArtifactAccessError("ARTIFACT_INVALID", "artifact metadata is invalid")
        return data

    def _write_metadata(self, identifier: str, metadata: Mapping[str, Any]) -> None:
        path = self._metadata_path(identifier)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(metadata, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _metadata_path(self, identifier: str) -> Path:
        if not identifier or any(character in identifier for character in "/\\:"):
            raise ArtifactAccessError("ARTIFACT_INVALID", "artifact id is invalid")
        path = (self._root / f"{identifier}.json").resolve()
        if self._root not in path.parents:
            raise ArtifactAccessError("ARTIFACT_INVALID", "artifact id escapes the metadata root")
        return path


def _handle_from_metadata(identifier: str, metadata: Mapping[str, Any]) -> ArtifactHandle:
    try:
        return ArtifactHandle(
            id=identifier,
            content_hash=str(metadata["content_hash"]),
            media_type=str(metadata["media_type"]),
            size_bytes=int(metadata["size_bytes"]),
            sensitivity=Sensitivity(str(metadata["sensitivity"])),
            storage_key=str(metadata["storage_key"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactAccessError("ARTIFACT_INVALID", "artifact metadata is incomplete") from exc


__all__ = [
    "CAPABILITY_ARTIFACT_READ",
    "CAPABILITY_ARTIFACT_WRITE",
    "FileExtensionArtifactAccess",
]
