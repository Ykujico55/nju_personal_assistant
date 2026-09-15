"""Platform-neutral ports used by the core."""

from personal_assistant.core.secrets import SecretStorePort

from .ports import (
    DesktopInteractionPort,
    FileChange,
    FileWatcherPort,
    PathPolicyPort,
    ProcessHandle,
    ProcessSpec,
    ProcessSupervisorPort,
    ServiceManagerPort,
)

__all__ = [
    "DesktopInteractionPort",
    "FileChange",
    "FileWatcherPort",
    "PathPolicyPort",
    "ProcessHandle",
    "ProcessSpec",
    "ProcessSupervisorPort",
    "SecretStorePort",
    "ServiceManagerPort",
]
