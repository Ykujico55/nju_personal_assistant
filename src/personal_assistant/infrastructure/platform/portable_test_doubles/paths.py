from __future__ import annotations

from pathlib import Path


class ManagedPathPolicy:
    """Portable path policy used in tests; rejects traversal outside configured roots."""

    def __init__(self, *, readable_roots: tuple[Path, ...], artifact_root: Path) -> None:
        self._readable_roots = tuple(root.resolve() for root in readable_roots)
        self._artifact_root = artifact_root.resolve()

    def require_readable(self, path: Path) -> Path:
        resolved = path.resolve()
        if not any(resolved == root or root in resolved.parents for root in self._readable_roots):
            raise PermissionError(f"path is outside readable roots: {resolved}")
        return resolved

    def require_managed_artifact_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self._artifact_root and self._artifact_root not in resolved.parents:
            raise PermissionError(f"path is outside managed artifact root: {resolved}")
        return resolved

