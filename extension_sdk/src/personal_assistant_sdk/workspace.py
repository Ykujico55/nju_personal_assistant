"""Pure-data projection of the host's applied instance workspace.

Only an explicit local extension binding opts in. This is not an OS sandbox;
trusted extensions must still check paths, versions and writable modes themselves.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .models import RuntimeContext

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class WorkspaceConfigurationError(ValueError):
    """A content-free configuration rejection; never contains a local path."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def workspace_config(runtime: RuntimeContext, *, include_modes: bool = True) -> dict[str, Any]:
    """Return one configuration snapshot, never a union with legacy roots.

    Private binding fields are derived from the host envelope, not plugin config.
    Unbound legacy extensions continue to use their original configuration.
    """
    config: dict[str, Any] = dict(runtime.non_secret_config)
    if "workspace_binding" not in config:
        return config
    if config.pop("workspace_binding") != "instance" or "roots" in config:
        raise WorkspaceConfigurationError("WORKSPACE_BINDING_INVALID")
    workspace = getattr(runtime, "workspace", None)
    if workspace is None:
        raise WorkspaceConfigurationError("WORKSPACE_UNAVAILABLE")
    if not isinstance(workspace, Mapping) or set(workspace) != {
        "workspace_id",
        "revision",
        "roots",
    }:
        raise WorkspaceConfigurationError("WORKSPACE_CONFIGURATION_INVALID")
    for field in ("workspace_id", "revision"):
        if not isinstance(workspace[field], str) or not _KEY.fullmatch(workspace[field]):
            raise WorkspaceConfigurationError("WORKSPACE_CONFIGURATION_INVALID")
    raw_roots = workspace["roots"]
    if not isinstance(raw_roots, (list, tuple)) or not 1 <= len(raw_roots) <= 16:
        raise WorkspaceConfigurationError("WORKSPACE_ROOTS_EMPTY")
    roots: list[dict[str, Any]] = []
    keys: set[str] = set()
    for root in raw_roots:
        if not isinstance(root, Mapping) or set(root) != {"key", "label", "path", "mode"}:
            raise WorkspaceConfigurationError("WORKSPACE_CONFIGURATION_INVALID")
        key, label, path, mode = (root[name] for name in ("key", "label", "path", "mode"))
        if (
            not isinstance(key, str)
            or len(key) > 64
            or not _KEY.fullmatch(key)
            or key in keys
            or not isinstance(label, str)
            or not 1 <= len(label) <= 120
            or not isinstance(path, str)
            or not 1 <= len(path) <= 4096
            or any(ord(c) < 32 for c in label + path)
            or mode not in ("read_only", "read_write")
        ):
            raise WorkspaceConfigurationError("WORKSPACE_CONFIGURATION_INVALID")
        keys.add(key)
        projected = {"key": key, "label": label, "path": path}
        if include_modes:
            projected["mode"] = mode
        roots.append(projected)
    config.update(
        roots=roots,
        _workspace_id=workspace["workspace_id"],
        _workspace_revision=workspace["revision"],
    )
    return config
