"""Host-side extension discovery, registry, lifecycle, and worker supervision."""

from .config import ExtensionConfigError, ExtensionConfigStore, validate_extension_config
from .data_access import (
    HOST_DATA_METHODS,
    DataAccessError,
    ExtensionDataAccess,
    ExtensionDataContext,
)
from .errors import (
    ConfirmationRequiredError,
    DuplicateCapabilityError,
    ExtensionError,
    ExtensionOperationError,
    InvalidLifecycleTransition,
    ManifestValidationError,
    RpcCallError,
    RpcTimeoutError,
)
from .manifest import ExtensionManifest, ManifestParser, compute_artifact_hash
from .models import (
    CapabilityOwner,
    ExtensionRecord,
    ExtensionState,
    RegistrySnapshot,
    data_namespace,
)
from .operations import (
    DIAGNOSTIC_CODES,
    TERMINAL_OPERATION_STATES,
    ExtensionOperation,
    ExtensionOperationStore,
    OperationState,
)
from .registry import ExtensionRegistry
from .supervision import ExtensionRuntime, ExtensionSupervisorService

__all__ = [
    "DIAGNOSTIC_CODES",
    "TERMINAL_OPERATION_STATES",
    "CapabilityOwner",
    "ConfirmationRequiredError",
    "DataAccessError",
    "ExtensionConfigError",
    "ExtensionConfigStore",
    "ExtensionDataAccess",
    "ExtensionDataContext",
    "DuplicateCapabilityError",
    "ExtensionError",
    "ExtensionManifest",
    "ExtensionOperation",
    "ExtensionOperationError",
    "ExtensionOperationStore",
    "ExtensionRecord",
    "ExtensionRegistry",
    "ExtensionRuntime",
    "ExtensionState",
    "ExtensionSupervisorService",
    "HOST_DATA_METHODS",
    "InvalidLifecycleTransition",
    "ManifestParser",
    "ManifestValidationError",
    "OperationState",
    "RegistrySnapshot",
    "RpcCallError",
    "RpcTimeoutError",
    "compute_artifact_hash",
    "data_namespace",
    "validate_extension_config",
]
