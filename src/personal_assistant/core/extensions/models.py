"""Host-owned lifecycle and immutable registry models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manifest import ExtensionManifest


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
