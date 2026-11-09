"""Checked UTF-8 file access to instance-wide, explicitly authorized roots.

The checks reject links and stale versions, and replacement is atomic for readers.
A portable SHA check followed by os.replace is not an operating-system CAS against
an unrelated editor.  The service serializes cooperating extension writers; a
non-cooperating local writer can still race the last check and the rename.  These
checks are an accidental-access boundary for trusted code, not a malicious-code
sandbox.  No platform-specific API or host internal is imported here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .errors import FilesError

MAX_FILE_BYTES = 256 * 1024
MAX_LIST_ENTRIES = 10_000
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_RESERVED = re.compile(
    r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)",
    re.IGNORECASE,
)
# Ordinary dot files (e.g. .notes.md) are allowed.  Control/credential stores
# below these names and dotenv/key files are deliberately excluded.
_DENIED = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".ssh",
        ".aws",
        ".azure",
        ".gnupg",
        ".kube",
        ".codex",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
    }
)
_STATE_FILES = ("files.sqlite3", "files.sqlite3-journal", "files.sqlite3-wal", "files.sqlite3-shm")


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _version(value: os.stat_result) -> tuple[int, int, int, int]:
    # Windows path stat and fstat do not share ctime semantics on Python 3.12.
    return (*_identity(value), value.st_size, value.st_mtime_ns)


def _reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _no_links(path: Path) -> None:
    for component in (*reversed(path.parents), path):
        if _reparse(component.lstat()):
            raise FilesError("PATH_INVALID")


def _blocked(part: str) -> bool:
    folded = part.casefold()
    return (
        folded in _DENIED
        or folded == ".env"
        or folded.startswith(".env.")
        or folded.startswith(".personal-files-")
    )


def _parts(value: str, *, directory: bool = False) -> tuple[str, ...]:
    if directory and value == "":
        return ()
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise FilesError("PATH_INVALID")
    parts = tuple(value.replace("\\", "/").split("/"))
    if len(parts) > 32:
        raise FilesError("PATH_INVALID")
    for part in parts:
        if (
            not part
            or part in {".", ".."}
            or len(part) > 180
            or part != part.rstrip(" .")
            or _blocked(part)
            or any(ord(char) < 32 or ord(char) == 127 for char in part)
            or any(char in '<>:"|?*' for char in part)
            or _RESERVED.match(part)
        ):
            raise FilesError("PATH_INVALID")
    return parts


def _absolute(value: object) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or value != value.strip()
        or value.startswith(("\\\\", "//"))
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise FilesError("CONFIG_INVALID")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise FilesError("CONFIG_INVALID")
    # The drive/root anchor alone is exempt from colon/name checks.
    for component in path.parts[1:]:
        if (
            component != component.rstrip(" .")
            or _RESERVED.match(component)
            or any(char in '<>:"|?*' for char in component)
        ):
            raise FilesError("CONFIG_INVALID")
    return path


def _encode(content: str) -> bytes:
    if not isinstance(content, str) or "\x00" in content:
        raise FilesError("TEXT_REQUIRED")
    try:
        result = content.encode("utf-8", errors="strict")
    except UnicodeError:
        raise FilesError("TEXT_REQUIRED") from None
    if len(result) > MAX_FILE_BYTES:
        raise FilesError("FILE_TOO_LARGE")
    return result


def _open_read(path: Path) -> BinaryIO:
    return path.open("rb")


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    content: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class _Root:
    key: str
    label: str
    path: Path
    mode: str
    identity: tuple[int, int]


class FileAccess:
    """Configuration is independent of task and run IDs, shared by the instance."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        if not isinstance(config, Mapping) or "workspace_binding" in config:
            raise FilesError("CONFIG_INVALID")
        raw_roots = config.get("roots")
        if not isinstance(raw_roots, list) or not 1 <= len(raw_roots) <= 16:
            raise FilesError("CONFIG_INVALID")
        roots: dict[str, _Root] = {}
        try:
            state = _absolute(config.get("state_directory"))
            for raw in raw_roots:
                if not isinstance(raw, Mapping) or set(raw) - {"key", "label", "path", "mode"}:
                    raise FilesError("CONFIG_INVALID")
                key, label, mode = raw.get("key"), raw.get("label", raw.get("key")), raw.get("mode")
                if (
                    not isinstance(key, str)
                    or not _KEY.fullmatch(key)
                    or key in roots
                    or not isinstance(label, str)
                    or not label
                    or len(label) > 120
                    or any(ord(char) < 32 for char in label)
                    or mode not in {"read_only", "read_write"}
                ):
                    raise FilesError("CONFIG_INVALID")
                path = _absolute(raw.get("path"))
                if any(_blocked(part) for part in path.parts[1:]):
                    raise FilesError("CONFIG_INVALID")
                _no_links(path)
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise FilesError("CONFIG_INVALID")
                resolved = path.resolve(strict=True)
                # Windows short names can conceal an excluded component in
                # the configured spelling, including the root itself.
                if any(_blocked(part) for part in resolved.parts[1:]):
                    raise FilesError("CONFIG_INVALID")
                if any(
                    resolved.is_relative_to(other.path) or other.path.is_relative_to(resolved)
                    for other in roots.values()
                ):
                    raise FilesError("CONFIG_INVALID")
                roots[key] = _Root(key, label, resolved, mode, _identity(info))
            # Setup must create the state directory explicitly. Construction
            # is also used by READ tools/context and must have no write effects.
            _no_links(state)
            if not stat.S_ISDIR(state.lstat().st_mode):
                raise FilesError("CONFIG_INVALID")
            resolved_state = state.resolve(strict=True)
            if any(
                resolved_state.is_relative_to(root.path) or root.path.is_relative_to(resolved_state)
                for root in roots.values()
            ):
                raise FilesError("CONFIG_INVALID")
            self.state_directory = state.resolve(strict=True)
            self._state_identity = _identity(self.state_directory.lstat())
            self._roots = roots
            self.validate_state()
            binding = [
                {
                    "key": root.key,
                    "path": os.path.normcase(str(root.path)),
                    "identity": root.identity,
                    "mode": root.mode,
                }
                for root in sorted(roots.values(), key=lambda item: item.key)
            ]
            canonical: dict[str, object] = {
                "roots": binding,
                "state": os.path.normcase(str(self.state_directory)),
                "state_identity": self._state_identity,
            }
            if "_workspace_revision" in config:
                canonical["workspace"] = {
                    "id": config.get("_workspace_id"),
                    "revision": config["_workspace_revision"],
                }
            self.configuration_hash = hashlib.sha256(
                json.dumps(
                    canonical,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
        except (FilesError, OSError, RuntimeError, ValueError, TypeError):
            raise FilesError("CONFIG_INVALID") from None

    def validate_state(self) -> None:
        try:
            _no_links(self.state_directory)
            info = self.state_directory.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or _identity(info) != self._state_identity
                or self.state_directory.resolve(strict=True) != self.state_directory
            ):
                raise FilesError("STATE_CHANGED")
            for filename in _STATE_FILES:
                path = self.state_directory / filename
                try:
                    current = path.lstat()
                except FileNotFoundError:
                    continue
                if _reparse(current) or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                    raise FilesError("STATE_CHANGED")
        except (FilesError, OSError, RuntimeError, ValueError):
            raise FilesError("STATE_CHANGED") from None

    def _root(self, key: str) -> _Root:
        root = self._roots.get(key) if isinstance(key, str) else None
        if root is None:
            raise FilesError("ROOT_NOT_AUTHORIZED")
        self._assert_root(root)
        return root

    @staticmethod
    def _assert_root(root: _Root) -> None:
        try:
            _no_links(root.path)
            info = root.path.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or _identity(info) != root.identity
                or root.path.resolve(strict=True) != root.path
            ):
                raise FilesError("ROOT_CHANGED")
        except (FilesError, OSError, RuntimeError, ValueError):
            raise FilesError("ROOT_CHANGED") from None

    def target_key(self, root_key: str, path: str) -> str:
        """Stable target-location fingerprint, not an inode or root-key identity.

        A receipt-unknown operation must remain blocked at the same location
        across a root rename in configuration or a file replacement.
        """
        root = self._root(root_key)
        target, _, parents = self._route(root, _parts(path), missing_ok=True)
        self._assert_parents(root, parents)
        return hashlib.sha256(os.path.normcase(str(target)).encode("utf-8")).hexdigest()

    def roots_view(self) -> list[dict[str, Any]]:
        for root in self._roots.values():
            self._assert_root(root)
        return [
            {"key": root.key, "label": root.label, "mode": root.mode}
            for root in self._roots.values()
        ]

    def ensure_writable(self, root_key: str) -> None:
        if self._root(root_key).mode != "read_write":
            raise FilesError("READ_ONLY")

    def _route(
        self,
        root: _Root,
        parts: tuple[str, ...],
        *,
        directory: bool = False,
        missing_ok: bool = False,
        must_be_absent: bool = False,
    ) -> tuple[Path, os.stat_result | None, tuple[tuple[Path, tuple[int, int]], ...]]:
        self._assert_root(root)
        path = root.path
        info: os.stat_result | None = root.path.lstat()
        parents = [(root.path, root.identity)]
        try:
            for index, part in enumerate(parts):
                path = path / part
                last = index == len(parts) - 1
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    if last and missing_ok and not directory:
                        return path, None, tuple(parents)
                    raise FilesError("FILE_NOT_FOUND") from None
                if last and must_be_absent:
                    raise FilesError("VERSION_CONFLICT")
                if _reparse(info):
                    raise FilesError("PATH_INVALID")
                # Check every existing component before a missing leaf can
                # return. In particular, 8.3 aliases must never produce a
                # second target key or bypass excluded directory names.
                if path.resolve(strict=True) != path or not path.is_relative_to(root.path):
                    raise FilesError("PATH_INVALID")
                if not last or directory:
                    if not stat.S_ISDIR(info.st_mode):
                        raise FilesError("PATH_INVALID")
                    parents.append((path, _identity(info)))
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise FilesError("PATH_INVALID")
            if path.resolve(strict=True) != path or not path.is_relative_to(root.path):
                raise FilesError("PATH_INVALID")
            return path, info, tuple(parents)
        except FilesError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise FilesError("FILE_UNAVAILABLE") from None

    def _assert_parents(
        self,
        root: _Root,
        parents: tuple[tuple[Path, tuple[int, int]], ...],
    ) -> None:
        self._assert_root(root)
        try:
            for path, identity in parents:
                _no_links(path)
                current = path.lstat()
                if not stat.S_ISDIR(current.st_mode) or _identity(current) != identity:
                    raise FilesError("PATH_CHANGED")
        except (FilesError, OSError, RuntimeError, ValueError):
            raise FilesError("PATH_CHANGED") from None

    def snapshot(self, root_key: str, path: str, missing_ok: bool = False) -> FileSnapshot | None:
        root = self._root(root_key)
        target, before, parents = self._route(root, _parts(path), missing_ok=missing_ok)
        if before is None:
            self._assert_parents(root, parents)
            return None
        if before.st_size > MAX_FILE_BYTES:
            raise FilesError("FILE_TOO_LARGE")
        try:
            with _open_read(target) as stream:
                opened = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_nlink != 1
                    or _version(opened) != _version(before)
                ):
                    raise FilesError("FILE_CHANGED")
                data = stream.read(MAX_FILE_BYTES + 1)
                after = os.fstat(stream.fileno())
            if len(data) > MAX_FILE_BYTES:
                raise FilesError("FILE_TOO_LARGE")
            _, current, _ = self._route(root, _parts(path))
            self._assert_parents(root, parents)
            if (
                current is None
                or _version(current) != _version(opened)
                or _version(after) != _version(opened)
                or after.st_nlink != 1
                or len(data) != opened.st_size
            ):
                raise FilesError("FILE_CHANGED")
            text = data.decode("utf-8", errors="strict")
            if "\x00" in text:
                raise FilesError("TEXT_REQUIRED")
            return FileSnapshot(text, hashlib.sha256(data).hexdigest(), len(data))
        except FilesError:
            raise
        except UnicodeError:
            raise FilesError("TEXT_REQUIRED") from None
        except (OSError, RuntimeError, ValueError):
            raise FilesError("FILE_UNAVAILABLE") from None

    def list(
        self, root_key: str, path: str = "", offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        if (
            type(offset) is not int
            or offset < 0
            or offset > MAX_LIST_ENTRIES
            or type(limit) is not int
            or not 1 <= limit <= 200
        ):
            raise FilesError("INVALID_PAGE")
        root = self._root(root_key)
        parts = _parts(path, directory=True)
        directory, _, parents = self._route(root, parts, directory=True)
        entries: list[dict[str, Any]] = []
        skipped = 0
        try:
            with os.scandir(directory) as listing:
                for index, entry in enumerate(listing):
                    if index >= MAX_LIST_ENTRIES:
                        raise FilesError("DIRECTORY_TOO_LARGE")
                    try:
                        _parts(entry.name)
                        # Windows DirEntry.stat may omit inode/link-count fields.
                        current = Path(entry.path).lstat()
                        if (
                            _reparse(current)
                            or not (stat.S_ISDIR(current.st_mode) or stat.S_ISREG(current.st_mode))
                            or (stat.S_ISREG(current.st_mode) and current.st_nlink != 1)
                        ):
                            skipped += 1
                            continue
                        relative = "/".join((*parts, entry.name))
                        entries.append(
                            {
                                "name": entry.name,
                                "path": relative,
                                "kind": "directory" if stat.S_ISDIR(current.st_mode) else "file",
                                "size_bytes": current.st_size
                                if stat.S_ISREG(current.st_mode)
                                else 0,
                            }
                        )
                    except (FilesError, OSError):
                        skipped += 1
            self._assert_parents(root, parents)
        except FilesError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise FilesError("FILE_UNAVAILABLE") from None
        entries.sort(key=lambda item: (item["name"].casefold(), item["name"]))
        end = offset + limit
        return {
            "root_key": root_key,
            "path": "/".join(parts),
            "entries": entries[offset:end],
            "next_offset": end if end < len(entries) else None,
            "skipped": skipped,
        }

    def directory_absent(self, root_key: str, path: str) -> None:
        """Validate a single new directory without creating its parents."""
        self.ensure_writable(root_key)
        root = self._root(root_key)
        _, _, parents = self._route(root, _parts(path), missing_ok=True, must_be_absent=True)
        self._assert_parents(root, parents)

    def mkdir(self, root_key: str, path: str) -> None:
        """Create one absent directory, retaining uncertain effects for review."""
        self.ensure_writable(root_key)
        root = self._root(root_key)
        parts = _parts(path)
        target, _, parents = self._route(root, parts, missing_ok=True, must_be_absent=True)
        self._assert_parents(root, parents)
        created = False
        try:
            target.mkdir()
            created = True
            observed = target.lstat()
            _, current, checked = self._route(root, parts, directory=True)
            self._assert_parents(root, parents)
            self._assert_parents(root, checked)
            if current is None or _identity(observed) != _identity(current):
                raise FilesError("WRITE_RESULT_UNKNOWN")
        except FileExistsError:
            raise FilesError("WRITE_RESULT_UNKNOWN" if created else "VERSION_CONFLICT") from None
        except (FilesError, OSError, RuntimeError, ValueError):
            # Once mkdir was attempted, a failed postcondition or uncertain
            # syscall result cannot authorize retry or removal of the target.
            raise FilesError("WRITE_RESULT_UNKNOWN") from None

    @staticmethod
    def _expected(expected: str | None) -> None:
        if expected is not None and (
            not isinstance(expected, str) or not _HASH.fullmatch(expected)
        ):
            raise FilesError("HASH_REQUIRED")

    @staticmethod
    def _compare(current: FileSnapshot | None, expected: str | None) -> None:
        if (current.sha256 if current is not None else None) != expected:
            raise FilesError("VERSION_CONFLICT")

    def write(
        self, root_key: str, path: str, content: str, expected_sha256: str | None
    ) -> FileSnapshot:
        self.ensure_writable(root_key)
        self._expected(expected_sha256)
        data = _encode(content)
        root = self._root(root_key)
        target, _, parents = self._route(root, _parts(path), missing_ok=True)
        self._compare(self.snapshot(root_key, path, missing_ok=True), expected_sha256)
        # Revalidate before making any temporary file; no new parent directories.
        self._compare(self.snapshot(root_key, path, missing_ok=True), expected_sha256)
        self._assert_parents(root, parents)
        temporary = target.parent / f".personal-files-{uuid.uuid4().hex}.tmp"
        published = False
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                # Keep the temporary handle open during the final checks. On
                # Windows its sharing mode also prevents moving this parent
                # directory while these checks run. Closing before rename is
                # unavoidable with portable APIs; the documented race remains.
                self._assert_parents(root, parents)
                self._compare(self.snapshot(root_key, path, missing_ok=True), expected_sha256)
                self._assert_parents(root, parents)
            if expected_sha256 is None:
                if os.name == "nt":
                    # Windows rename refuses an existing destination.  replace
                    # would silently clobber a file created after the last check.
                    os.rename(temporary, target)
                    published = True
                else:
                    # Atomic no-replace publication on POSIX.  Drop our only
                    # extra hard link immediately, before returning a snapshot.
                    os.link(temporary, target)
                    published = True
                    temporary.unlink()
            else:
                os.replace(temporary, target)
                published = True
            self._assert_parents(root, parents)
            result = self.snapshot(root_key, path)
            if result is None or result.sha256 != hashlib.sha256(data).hexdigest():
                raise FilesError("WRITE_RESULT_UNKNOWN")
            return result
        except FileExistsError:
            raise FilesError("WRITE_RESULT_UNKNOWN" if published else "VERSION_CONFLICT") from None
        except FilesError:
            if published:
                raise FilesError("WRITE_RESULT_UNKNOWN") from None
            raise
        except (OSError, RuntimeError, ValueError):
            raise FilesError("WRITE_RESULT_UNKNOWN" if published else "FILE_UNAVAILABLE") from None
        finally:
            # Cleanup is only allowed on the same authorized parent identity.
            # A concurrently moved/replaced directory must never redirect unlink.
            try:
                self._assert_parents(root, parents)
                temporary.unlink(missing_ok=True)
            except (FilesError, OSError):
                pass

    def delete(self, root_key: str, path: str, expected_sha256: str) -> None:
        """Low-level CAS only; the service/CLI must consume human confirmation."""
        self.ensure_writable(root_key)
        self._expected(expected_sha256)
        if expected_sha256 is None:
            raise FilesError("HASH_REQUIRED")
        root = self._root(root_key)
        target, _, parents = self._route(root, _parts(path))
        self._compare(self.snapshot(root_key, path), expected_sha256)
        self._assert_parents(root, parents)
        self._compare(self.snapshot(root_key, path), expected_sha256)
        self._assert_parents(root, parents)
        deleted = False
        try:
            target.unlink()
            deleted = True
            self._assert_parents(root, parents)
        except (FilesError, OSError, RuntimeError, ValueError):
            raise FilesError("WRITE_RESULT_UNKNOWN" if deleted else "FILE_UNAVAILABLE") from None
