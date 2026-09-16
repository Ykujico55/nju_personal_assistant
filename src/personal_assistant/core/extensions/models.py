"""Host-owned lifecycle and immutable registry models."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manifest import ExtensionManifest


def data_namespace(extension_id: str) -> str:
    """Host-owned ``ext_*`` schema name for extension business data.

    The encoding is injective: ``[a-z0-9]`` characters are kept, everything else
    becomes ``_<codepoint hex>_``.  ``a.b``, ``a_b`` and ``a-b`` therefore map to
    different namespaces.  Long identifiers keep a digest suffix so the name
    stays a valid PostgreSQL identifier (max 63 bytes).
    """

    safe_characters = "abcdefghijklmnopqrstuvwxyz0123456789"
    encoded = "".join(
        character if character in safe_characters else f"_{ord(character):x}_"
        for character in extension_id
    )
    namespace = f"ext_{encoded}"
    if len(namespace) <= 63:
        return namespace
    digest = hashlib.sha256(extension_id.encode("utf-8")).hexdigest()[:16]
    return f"ext_{encoded[:40]}_{digest}"


class ExtensionState(StrEnum):
    DISCOVERED = "DISCOVERED"
    STAGED = "STAGED"
    REJECTED = "REJECTED"
    INSTALLED_DISABLED = "INSTALLED_DISABLED"
    STARTING = "STARTING"
    ENABLED = "ENABLED"
    QUARANTINED = "QUARANTINED"
    DRAINING = "DRAINING"
    DISABLED = "DISABLED"
    UNINSTALLING = "UNINSTALLING"
    UNINSTALLED = "UNINSTALLED"
    UPGRADING = "UPGRADING"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True, slots=True)
class ExtensionRecord:
    manifest: ExtensionManifest
    artifact_hash: str
    state: ExtensionState
    install_path: str | None = None
    data_retained: bool = True
    tombstone: bool = False


@dataclass(frozen=True, slots=True)
class CapabilityOwner:
    extension_id: str
    extension_version: str
    slot: str


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    """An immutable routing view pinned by an agent run."""

    generation: int
    extensions: Mapping[str, ExtensionRecord] = field(default_factory=dict)
    capabilities: Mapping[str, CapabilityOwner] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extensions", MappingProxyType(dict(self.extensions)))
        object.__setattr__(self, "capabilities", MappingProxyType(dict(self.capabilities)))

    def owner_of(self, capability_id: str) -> CapabilityOwner | None:
        return self.capabilities.get(capability_id)
