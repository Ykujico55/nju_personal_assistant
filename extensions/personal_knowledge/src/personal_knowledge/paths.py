"""Authorized roots and path safety.

Every read resolves the real path and proves the target is still inside one of
the user-authorized roots.  ``..`` traversal, absolute paths, Windows drive
paths, out-of-root symlinks and junctions, and case-only bypasses are rejected.
Files are only ever opened read-only; nothing here writes, renames or touches
timestamps of user files.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .models import ALLOWED_SUFFIXES, MEDIA_TYPE_BY_SUFFIX, ExtractionDiagnosticCode

MAX_ROOTS = 16
MAX_RECORDED_DIAGNOSTICS = 64
DEFAULT_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)


class RootConfigurationError(ValueError):
    """Typed configuration rejection; never contains file contents."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PathSafetyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RootSpec:
    key: str
    path: str
    label: str = ""


@dataclass(frozen=True, slots=True)
class AuthorizedRoot:
    key: str
    path: Path
    label: str = ""

    def contains(self, candidate: Path) -> bool:
        return is_within(self.path, candidate)

    def resolve_relative(self, relative_path: str) -> Path:
        return safe_join(self.path, relative_path)

    def read_bytes(self, relative_path: str, *, max_bytes: int | None = None) -> bytes:
        return safe_read_bytes(self.path, relative_path, max_bytes=max_bytes)


def parse_roots(
    raw: object,
    *,
    default_key_prefix: str = "root",
) -> tuple[RootSpec, ...]:
    """Parse the ``roots`` config value into validated, resolved root specs."""

    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, Mapping)):
        raise RootConfigurationError("roots_invalid", "roots must be an array")
    items = list(raw)
    if not items:
        raise RootConfigurationError("roots_empty", "at least one authorized root is required")
    if len(items) > MAX_ROOTS:
        raise RootConfigurationError("roots_too_many", f"at most {MAX_ROOTS} roots are supported")
    specs: list[RootSpec] = []
    keys: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise RootConfigurationError("root_invalid", "each root must be an object")
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise RootConfigurationError(
                "root_path_invalid", "root path must be a non-empty string"
            )
        if len(raw_path) > 1024:
            raise RootConfigurationError("root_path_invalid", "root path is too long")
        key = item.get("key")
        if key is None:
            key = f"{default_key_prefix}-{index + 1}"
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key):
            raise RootConfigurationError(
                "root_key_invalid", "root key must be 1-64 safe characters"
            )
        if key in keys:
            raise RootConfigurationError("root_key_duplicate", "root keys must be unique")
        keys.add(key)
        label = item.get("label", "")
        if not isinstance(label, str) or len(label) > 128:
            raise RootConfigurationError("root_label_invalid", "root label is too long")
        specs.append(RootSpec(key=key, path=raw_path.strip(), label=label))
    resolved = resolve_roots(specs)
    _reject_overlapping_roots(resolved)
    return tuple(specs)


def resolve_roots(specs: Iterable[RootSpec]) -> tuple[AuthorizedRoot, ...]:
    roots: list[AuthorizedRoot] = []
    for spec in specs:
        raw = Path(spec.path).expanduser()
        try:
            resolved = raw.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RootConfigurationError(
                "root_unavailable", f"authorized root is not readable: {spec.key}"
            ) from exc
        if not resolved.is_dir():
            raise RootConfigurationError(
                "root_not_directory", f"authorized root is not a directory: {spec.key}"
            )
        roots.append(AuthorizedRoot(key=spec.key, path=resolved, label=spec.label))
    return tuple(roots)


def _reject_overlapping_roots(roots: tuple[AuthorizedRoot, ...]) -> None:
    for index, left in enumerate(roots):
        for right in roots[index + 1 :]:
            if is_within(left.path, right.path) or is_within(right.path, left.path):
                raise RootConfigurationError(
                    "roots_overlap",
                    "authorized roots must not contain each other (duplicate indexing)",
                )


def is_within(root: Path, candidate: Path) -> bool:
    """Case-insensitive containment check on the resolved real paths."""

    root_text = os.path.normcase(str(Path(root).resolve()))
    candidate_text = os.path.normcase(str(Path(candidate).resolve()))
    if root_text == candidate_text:
        return True
    try:
        return os.path.commonpath([root_text, candidate_text]) == root_text
    except ValueError:
        return False


def safe_join(root: Path, relative_path: str) -> Path:
    """Resolve a stored relative path under ``root`` or fail closed."""

    if not isinstance(relative_path, str) or not relative_path:
        raise PathSafetyError("path_invalid", "path must be a non-empty string")
    if len(relative_path) > 4096:
        raise PathSafetyError("path_invalid", "path is too long")
    normalized = relative_path.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise PathSafetyError("path_absolute", "absolute paths are not allowed")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise PathSafetyError("path_traversal", "path traversal is not allowed")
    if not parts:
        raise PathSafetyError("path_invalid", "path must name a file")
    candidate = root.joinpath(*parts)
    resolved = candidate.resolve(strict=False)
    if not is_within(root, resolved):
        raise PathSafetyError("path_escape", "path escapes the authorized root")
    return resolved


def safe_read_bytes(root: Path, relative_path: str, *, max_bytes: int | None = None) -> bytes:
    """Read one file after re-resolving it inside the root, read-only."""

    resolved = safe_join(root, relative_path)
    if not resolved.is_file():
        raise PathSafetyError("path_missing", "file is missing")
    if resolved.is_symlink() or _is_junction(resolved):
        raise PathSafetyError("path_symlink", "symbolic links and junctions are not read")
    size = resolved.stat().st_size
    if max_bytes is not None and size > max_bytes:
        raise PathSafetyError("file_too_large", "file exceeds the configured size limit")
    with open(resolved, "rb") as stream:
        data = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
    if max_bytes is not None and len(data) > max_bytes:
        # The file may grow after the pre-read stat.  Bound the bytes actually
        # consumed as well so a racing writer cannot bypass the memory limit.
        raise PathSafetyError("file_too_large", "file exceeds the configured size limit")
    # Re-resolve after the read instead of checking the stale pre-open Path.
    # A concurrently replaced symlink/junction must invalidate the bytes before
    # they can enter the index, even though the already-open descriptor itself
    # cannot be retroactively changed.
    current = safe_join(root, relative_path)
    if current != resolved or not is_within(root, current):
        raise PathSafetyError("path_escape", "path escaped the authorized root during the read")
    if not current.is_file() or current.is_symlink() or _is_junction(current):
        raise PathSafetyError("path_symlink", "file changed to a link during the read")
    return data


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def media_type_for(relative_path: str) -> str | None:
    lowered = relative_path.lower()
    for suffix in ALLOWED_SUFFIXES:
        if lowered.endswith(suffix):
            return MEDIA_TYPE_BY_SUFFIX[suffix]
    return None


@dataclass(frozen=True, slots=True)
class WalkedFile:
    relative_path: str
    absolute_path: Path
    media_type: str
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class WalkResult:
    files: tuple[WalkedFile, ...]
    diagnostics: tuple[tuple[str, ExtractionDiagnosticCode, str], ...] = ()


def walk_root(
    root: AuthorizedRoot,
    *,
    include_hidden: bool = False,
    ignored_directory_names: frozenset[str] = DEFAULT_IGNORED_DIRECTORY_NAMES,
) -> WalkResult:
    """Walk one authorized root without following links or junctions."""

    files: list[WalkedFile] = []
    diagnostics: list[tuple[str, ExtractionDiagnosticCode, str]] = []
    stack: list[tuple[Path, str]] = [(root.path, "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError:
            diagnostics.append(
                (prefix, ExtractionDiagnosticCode.READ_ERROR, "directory is unreadable")
            )
            continue
        for entry in entries:
            name = entry.name
            if not include_hidden and name.startswith("."):
                continue
            try:
                if entry.is_symlink() or entry.is_junction():
                    diagnostics.append(
                        (
                            f"{prefix}{name}",
                            ExtractionDiagnosticCode.READ_ERROR,
                            "skipped symbolic link or junction",
                        )
                    )
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if name in ignored_directory_names:
                        continue
                    stack.append((Path(entry.path), f"{prefix}{name}/"))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                relative = f"{prefix}{name}"
                media_type = media_type_for(relative)
                if media_type is None:
                    continue
                stat = entry.stat(follow_symlinks=False)
                files.append(
                    WalkedFile(
                        relative_path=relative,
                        absolute_path=Path(entry.path),
                        media_type=media_type,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                    )
                )
            except OSError:
                diagnostics.append(
                    (
                        f"{prefix}{name}",
                        ExtractionDiagnosticCode.READ_ERROR,
                        "entry is unreadable",
                    )
                )
    files.sort(key=lambda item: item.relative_path)
    bounded = tuple(diagnostics[:MAX_RECORDED_DIAGNOSTICS])
    return WalkResult(files=tuple(files), diagnostics=bounded)


def _is_junction(path: Path) -> bool:
    is_junction = getattr(Path, "is_junction", None)
    if is_junction is None:
        return False
    try:
        return bool(is_junction(path))
    except OSError:
        return True


__all__ = [
    "AuthorizedRoot",
    "DEFAULT_IGNORED_DIRECTORY_NAMES",
    "PathSafetyError",
    "RootConfigurationError",
    "RootSpec",
    "WalkResult",
    "WalkedFile",
    "hash_bytes",
    "is_within",
    "media_type_for",
    "parse_roots",
    "resolve_roots",
    "safe_join",
    "safe_read_bytes",
    "walk_root",
]
