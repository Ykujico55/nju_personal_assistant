"""Bounded read-only resume access under explicitly configured local directories.

Source files remain authoritative and untouched.  A local selection is imported
into the existing host artifact store by the Worker, never sent as model context.
This module intentionally provides no generic filesystem writer.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

MAX_FILE_BYTES = 512 * 1024
MAX_SCANNED_ENTRIES = 2000
MAX_DIRECTORY_DEPTH = 32
MEDIA_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.IGNORECASE)


class LocalResumeError(ValueError):
    """Public error text consists of a code, never an absolute source path."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _version(value: os.stat_result) -> tuple[int, int, int, int]:
    # Windows fstat and path stat expose different ctime semantics on Python
    # 3.12.  Compare their common stable version fields; the caller also binds
    # the exact contents with SHA-256 before importing an artifact.
    return (*_identity(value), value.st_size, value.st_mtime_ns)


def _reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _hidden(name: str, value: os.stat_result) -> bool:
    return name.startswith(".") or bool(
        getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_HIDDEN
    )


def _no_links(path: Path) -> None:
    # Check ancestors too: resolving a configured junction once must not grant
    # the linked destination as a new independently authorized root.
    for component in (*reversed(path.parents), path):
        if _reparse(component.lstat()):
            raise LocalResumeError("RESUME_PATH_INVALID")


def _relative_parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise LocalResumeError("RESUME_PATH_INVALID")
    parts = tuple(value.replace("\\", "/").split("/"))
    for part in parts:
        if (
            not part or part.startswith(".") or part != part.rstrip(" .")
            or any(ord(char) < 32 or ord(char) == 127 for char in part)
            or any(char in '<>:"|?*' for char in part)
            or _RESERVED.match(part)
        ):
            raise LocalResumeError("RESUME_PATH_INVALID")
    if len(parts[-1]) > 180:
        raise LocalResumeError("RESUME_PATH_INVALID")
    return parts


def _open_read(path: Path) -> BinaryIO:
    return path.open("rb")


def _bounded_read(stream: BinaryIO) -> bytes:
    return stream.read(MAX_FILE_BYTES + 1)


@dataclass(frozen=True, slots=True)
class _Root:
    key: str
    path: Path
    identity: tuple[int, int]


class ResumeDirectory:
    """A Worker-lifetime authorization snapshot of up to sixteen local roots."""

    def __init__(self, config_roots: object) -> None:
        if config_roots is None or config_roots == [] or config_roots == "":
            raise LocalResumeError("RESUME_ROOTS_REQUIRED")
        if not isinstance(config_roots, list) or not 1 <= len(config_roots) <= 16:
            raise LocalResumeError("RESUME_ROOT_INVALID")
        roots: dict[str, _Root] = {}
        try:
            for raw in config_roots:
                if not isinstance(raw, Mapping) or set(raw) != {"key", "path"}:
                    raise LocalResumeError("RESUME_ROOT_INVALID")
                key, value = raw["key"], raw["path"]
                if (
                    not isinstance(key, str) or not _KEY.fullmatch(key) or key in roots
                    or not isinstance(value, str) or not value
                    or value != value.strip() or len(value) > 1024
                    or value.startswith(("\\\\", "//"))
                ):
                    raise LocalResumeError("RESUME_ROOT_INVALID")
                path = Path(value)
                if not path.is_absolute():
                    raise LocalResumeError("RESUME_ROOT_INVALID")
                _no_links(path)
                current = path.lstat()
                if not stat.S_ISDIR(current.st_mode) or _hidden(path.name, current):
                    raise LocalResumeError("RESUME_ROOT_INVALID")
                resolved = path.resolve(strict=True)
                for other in roots.values():
                    if resolved.is_relative_to(other.path) or other.path.is_relative_to(resolved):
                        raise LocalResumeError("RESUME_ROOT_INVALID")
                roots[key] = _Root(key, resolved, _identity(current))
        except LocalResumeError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise LocalResumeError("RESUME_ROOT_INVALID") from None
        self._roots = roots

    def _root(self, root_key: str) -> _Root:
        root = self._roots.get(root_key) if isinstance(root_key, str) else None
        if root is None:
            raise LocalResumeError("RESUME_ROOT_NOT_AUTHORIZED")
        self._assert_root(root)
        return root

    @staticmethod
    def _assert_root(root: _Root) -> None:
        try:
            _no_links(root.path)
            current = root.path.lstat()
            if (
                not stat.S_ISDIR(current.st_mode) or _identity(current) != root.identity
                or root.path.resolve(strict=True) != root.path
            ):
                raise LocalResumeError("RESUME_ROOT_CHANGED")
        except (OSError, RuntimeError, ValueError):
            raise LocalResumeError("RESUME_ROOT_CHANGED") from None

    def _checked_file(self, root: _Root, relative_path: str) -> tuple[Path, os.stat_result]:
        self._assert_root(root)
        parts = _relative_parts(relative_path)
        path = root.path
        try:
            for index, part in enumerate(parts):
                path = path / part
                info = path.lstat()
                if _reparse(info) or _hidden(part, info):
                    raise LocalResumeError("RESUME_PATH_INVALID")
                if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
                    raise LocalResumeError("RESUME_PATH_INVALID")
            resolved = path.resolve(strict=True)
            if resolved != path or not resolved.is_relative_to(root.path):
                raise LocalResumeError("RESUME_PATH_INVALID")
            if not stat.S_ISREG(info.st_mode):
                raise LocalResumeError("RESUME_FILE_UNAVAILABLE")
            if path.suffix.lower() not in MEDIA_BY_SUFFIX:
                raise LocalResumeError("RESUME_MEDIA_UNSUPPORTED")
            if not 0 < info.st_size <= MAX_FILE_BYTES:
                raise LocalResumeError("RESUME_TOO_LARGE")
            return path, info
        except LocalResumeError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise LocalResumeError("RESUME_FILE_UNAVAILABLE") from None

    def _snapshot(self, root: _Root, relative_path: str) -> tuple[str, str, bytes]:
        path, before = self._checked_file(root, relative_path)
        try:
            with _open_read(path) as stream:
                opened = os.fstat(stream.fileno())
                # A path replacement between validation and open must fail
                # before any contents of the replacement can be consumed.
                if not stat.S_ISREG(opened.st_mode) or _version(opened) != _version(before):
                    raise LocalResumeError("RESUME_FILE_CHANGED")
                data = _bounded_read(stream)
                if len(data) > MAX_FILE_BYTES:
                    raise LocalResumeError("RESUME_TOO_LARGE")
                after = os.fstat(stream.fileno())
            _, current = self._checked_file(root, relative_path)
            if (
                _version(after) != _version(opened) or _version(current) != _version(opened)
                or len(data) != opened.st_size
            ):
                raise LocalResumeError("RESUME_FILE_CHANGED")
            self._assert_root(root)
            return path.name, MEDIA_BY_SUFFIX[path.suffix.lower()], data
        except LocalResumeError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise LocalResumeError("RESUME_FILE_UNAVAILABLE") from None

    def read_file(
        self, *, root_key: str, relative_path: str, expected_sha256: str
    ) -> tuple[str, str, bytes]:
        if not isinstance(expected_sha256, str) or not _HASH.fullmatch(expected_sha256):
            raise LocalResumeError("RESUME_HASH_REQUIRED")
        snapshot = self._snapshot(self._root(root_key), relative_path)
        if hashlib.sha256(snapshot[2]).hexdigest() != expected_sha256:
            raise LocalResumeError("RESUME_FILE_CHANGED")
        return snapshot

    def list_files(self, *, root_key: str | None = None, limit: int = 100) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise LocalResumeError("RESUME_LIMIT_INVALID")
        roots = (self._root(root_key),) if root_key is not None else tuple(self._roots.values())
        files: list[dict[str, Any]] = []
        scanned = 0
        skipped = 0
        truncated = False
        for root in roots:
            self._assert_root(root)
            pending = [(root.path, 0)]
            while pending:
                directory, depth = pending.pop()
                if depth > MAX_DIRECTORY_DEPTH:
                    skipped += 1
                    truncated = True
                    continue
                try:
                    self._assert_root(root)
                    _no_links(directory)
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            if scanned >= MAX_SCANNED_ENTRIES:
                                truncated = True
                                pending.clear()
                                break
                            scanned += 1
                            try:
                                info = entry.stat(follow_symlinks=False)
                                if _reparse(info) or _hidden(entry.name, info):
                                    skipped += 1
                                    continue
                                path = Path(entry.path)
                                relative = path.relative_to(root.path).as_posix()
                                if stat.S_ISDIR(info.st_mode):
                                    # Recheck this route when it is consumed:
                                    # a renamed directory cannot expand access.
                                    self._assert_root(root)
                                    _no_links(path)
                                    pending.append((path, depth + 1))
                                    continue
                                if path.suffix.lower() not in MEDIA_BY_SUFFIX:
                                    skipped += 1
                                    continue
                                filename, media_type, data = self._snapshot(root, relative)
                                if len(files) >= limit:
                                    truncated = True
                                    pending.clear()
                                    break
                                files.append({
                                    "root_key": root.key, "relative_path": relative,
                                    "filename": filename, "media_type": media_type,
                                    "size_bytes": len(data),
                                    "sha256": hashlib.sha256(data).hexdigest(),
                                })
                            except (LocalResumeError, OSError):
                                skipped += 1
                                continue
                except (LocalResumeError, OSError):
                    skipped += 1
                    truncated = True
                if truncated and (scanned >= MAX_SCANNED_ENTRIES or len(files) >= limit):
                    break
            self._assert_root(root)
            if truncated and (scanned >= MAX_SCANNED_ENTRIES or len(files) >= limit):
                break
        files.sort(key=lambda item: (item["root_key"], item["relative_path"]))
        return {"files": files, "truncated": truncated, "skipped": skipped}
