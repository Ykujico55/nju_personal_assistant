"""Filesystem adapters for staging, installing and supervising extensions.

These adapters keep third-party code at arm's length: staging never imports or
executes the artifact, installation happens only after the exact confirmation, and
worker processes isolate dependencies and crashes.  They are not a malicious-code
sandbox.
"""

from .config_store import FileExtensionConfigStore
from .data import InMemoryExtensionDataStore, PostgresExtensionDataStore
from .installer import VenvArtifactInstaller
from .processes import ProcessContractVerifier, ProcessRuntimeSupervisor
from .staging import LocalArtifactStager
from .versions import CompatibleVersionOperator, RetainedVersion, VersionCatalog

__all__ = [
    "CompatibleVersionOperator",
    "FileExtensionConfigStore",
    "InMemoryExtensionDataStore",
    "LocalArtifactStager",
    "PostgresExtensionDataStore",
    "ProcessContractVerifier",
    "ProcessRuntimeSupervisor",
    "RetainedVersion",
    "VenvArtifactInstaller",
    "VersionCatalog",
]
