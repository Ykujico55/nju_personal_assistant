"""Host-side extension discovery, registry, lifecycle, and worker supervision."""

from .errors import (
    ConfirmationRequiredError,
    DuplicateCapabilityError,
    ExtensionError,
    InvalidLifecycleTransition,
    ManifestValidationError,
    RpcCallError,
    RpcTimeoutError,
)
from .manifest import ExtensionManifest, ManifestParser, compute_artifact_hash
from .models import ExtensionRecord, ExtensionState, RegistrySnapshot
from .registry import ExtensionRegistry

__all__ = [
    "ConfirmationRequiredError",
    "DuplicateCapabilityError",
    "ExtensionError",
    "ExtensionManifest",
    "ExtensionRecord",
    "ExtensionRegistry",
    "ExtensionState",
    "InvalidLifecycleTransition",
    "ManifestParser",
    "ManifestValidationError",
    "RegistrySnapshot",
    "RpcCallError",
    "RpcTimeoutError",
    "compute_artifact_hash",
]
