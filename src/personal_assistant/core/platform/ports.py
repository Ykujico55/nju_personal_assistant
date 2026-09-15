"""Operating-system boundaries.

Core code imports these protocols, never Win32 or another platform SDK directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    argv: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str] = field(default_factory=dict)
    stdin_json_rpc: bool = True


@dataclass(frozen=True, slots=True)
class ProcessHandle:
    process_id: str
    operating_system_pid: int | None = None


@dataclass(frozen=True, slots=True)
class FileChange:
    path: Path
    kind: str


class ServiceManagerPort(Protocol):
    async def install(self, service_name: str, argv: Sequence[str]) -> None: ...

    async def start(self, service_name: str) -> None: ...

    async def stop(self, service_name: str) -> None: ...

    async def status(self, service_name: str) -> str: ...


class DesktopInteractionPort(Protocol):
    async def request_visible_browser(self, url: str, reason: str) -> str: ...

    async def close_session(self, session_id: str) -> None: ...


class ProcessSupervisorPort(Protocol):
    async def start(self, spec: ProcessSpec) -> ProcessHandle: ...

    async def terminate(self, handle: ProcessHandle, grace_seconds: float) -> None: ...

    async def is_alive(self, handle: ProcessHandle) -> bool: ...


class FileWatcherPort(Protocol):
    def watch(self, roots: Sequence[Path]) -> AsyncIterator[FileChange]: ...


class PathPolicyPort(Protocol):
    def require_readable(self, path: Path) -> Path: ...

    def require_managed_artifact_path(self, path: Path) -> Path: ...
