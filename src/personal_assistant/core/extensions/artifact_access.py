"""Generic host-owned artifact capability for trusted extensions.

Artifacts are content-addressed blobs owned by the host.  An extension may only
read or delete artifacts it created; ownership metadata is stored next to the
blob, never inside the extension's own SQL namespace.  This is an integrity
boundary for trusted extension code, not a hostile-code sandbox.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from personal_assistant.core.extensions.errors import ExtensionOperationError

HOST_ARTIFACT_PUT = "host.artifact.put"
HOST_ARTIFACT_READ = "host.artifact.read"
HOST_ARTIFACT_DELETE = "host.artifact.delete"
HOST_ARTIFACT_METHODS = frozenset(
    {HOST_ARTIFACT_PUT, HOST_ARTIFACT_READ, HOST_ARTIFACT_DELETE}
)

CAPABILITY_ARTIFACT_READ = "artifact.read"
CAPABILITY_ARTIFACT_WRITE = "artifact.write"

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024

_ARTIFACT_ERROR_CODES = frozenset(
    {
        "ARTIFACT_UNAVAILABLE",
        "ARTIFACT_NOT_FOUND",
        "ARTIFACT_FORBIDDEN",
        "ARTIFACT_INVALID",
        "ARTIFACT_TOO_LARGE",
        "ARTIFACT_PROTOCOL_ERROR",
    }
)

_MEDIA_TYPE_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]{1,64}/[A-Za-z0-9!#$&^_.+-]{1,64}$")


class ArtifactAccessError(ExtensionOperationError):
    def __init__(self, code: str, message: str) -> None:
        if code not in _ARTIFACT_ERROR_CODES:
            code = "ARTIFACT_INVALID"
        super().__init__(code, message)


@dataclass(frozen=True, slots=True)
class ExtensionArtifactContext:
    extension_id: str
    extension_version: str


@runtime_checkable
class ExtensionArtifactAccess(Protocol):
    @property
    def available(self) -> bool: ...

    async def handle(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        context: ExtensionArtifactContext,
    ) -> Any: ...


def decode_artifact_data(params: Mapping[str, Any]) -> bytes:
    encoded = params.get("data_base64")
    if not isinstance(encoded, str) or not encoded:
        raise ArtifactAccessError("ARTIFACT_INVALID", "artifact data must be base64 text")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ArtifactAccessError(
            "ARTIFACT_INVALID", "artifact data is not valid base64"
        ) from exc
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ArtifactAccessError("ARTIFACT_TOO_LARGE", "artifact exceeds the size limit")
    if not data:
        raise ArtifactAccessError("ARTIFACT_INVALID", "artifact must not be empty")
    return data


def normalize_media_type(value: Any) -> str:
    if not isinstance(value, str) or _MEDIA_TYPE_RE.fullmatch(value.strip()) is None:
        raise ArtifactAccessError("ARTIFACT_INVALID", "artifact media type is invalid")
    return value.strip().lower()


def artifact_id(params: Mapping[str, Any]) -> str:
    value = params.get("artifact_id")
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ArtifactAccessError("ARTIFACT_INVALID", "artifact id is invalid")
    return value


__all__ = [
    "CAPABILITY_ARTIFACT_READ",
    "CAPABILITY_ARTIFACT_WRITE",
    "HOST_ARTIFACT_DELETE",
    "HOST_ARTIFACT_METHODS",
    "HOST_ARTIFACT_PUT",
    "HOST_ARTIFACT_READ",
    "MAX_ARTIFACT_BYTES",
    "ArtifactAccessError",
    "ExtensionArtifactAccess",
    "ExtensionArtifactContext",
    "artifact_id",
    "decode_artifact_data",
    "normalize_media_type",
]
