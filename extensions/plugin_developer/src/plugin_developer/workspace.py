"""Text-only generated candidates, persisted baselines and exact immutable exports."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import sqlite3
import stat
import tomllib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from .validation import ValidationError, candidate_digest, candidate_files, snapshot_candidate

MAX_FILE_BYTES = 512 * 1024
MAX_CANDIDATE_BYTES = 2 * 1024 * 1024
MAX_FILES = 100
_SUFFIXES = frozenset({".py", ".toml", ".json", ".md", ".txt", ".lock"})
_ID = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+\Z")
_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:[-+][A-Za-z0-9.-]+)?\Z"
)
_CANDIDATE = re.compile(r"cand_[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.IGNORECASE)
_MARKER = ".plugin-developer.json"


class WorkspaceError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _identity(path: Path) -> tuple[int, int]:
    item = path.lstat()
    return item.st_dev, item.st_ino


def _safe_route(path: Path, *, directory: bool = False) -> None:
    for component in (*reversed(path.parents), path):
        item = component.lstat()
        if stat.S_ISLNK(item.st_mode) or (
            getattr(item, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise WorkspaceError("WORKSPACE_PATH_INVALID")
    if directory and not path.is_dir():
        raise WorkspaceError("WORKSPACE_PATH_INVALID")


def _parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise WorkspaceError("WORKSPACE_PATH_INVALID")
    parts = tuple(value.replace("\\", "/").split("/"))
    if len(parts) > 32:
        raise WorkspaceError("WORKSPACE_PATH_INVALID")
    for part in parts:
        if (
            not part
            or part.startswith(".")
            or part != part.rstrip(" .")
            or len(part) > 180
            or _RESERVED.match(part)
            or any(char in '<>:"|?*' or ord(char) < 32 or ord(char) == 127 for char in part)
        ):
            raise WorkspaceError("WORKSPACE_PATH_INVALID")
    if Path(parts[-1]).suffix.lower() not in _SUFFIXES:
        raise WorkspaceError("WORKSPACE_MEDIA_UNSUPPORTED")
    return parts


def _encode(content: str) -> bytes:
    if not isinstance(content, str) or any(
        ord(char) < 32 and char not in "\n\r\t" for char in content
    ):
        raise WorkspaceError("WORKSPACE_TEXT_REQUIRED")
    try:
        encoded = content.encode("utf-8")
    except UnicodeError:
        raise WorkspaceError("WORKSPACE_TEXT_REQUIRED") from None
    if len(encoded) > MAX_FILE_BYTES:
        raise WorkspaceError("WORKSPACE_FILE_TOO_LARGE")
    return encoded


def _file_version(item: os.stat_result) -> tuple[int, int, int, int]:
    return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns


class Workspace:
    def __init__(self, root: Path | str) -> None:
        try:
            path = Path(root)
            if not path.is_absolute():
                raise WorkspaceError("WORKSPACE_ROOT_INVALID")
            _safe_route(path, directory=True)
            self.root = path.resolve(strict=True)
            self._root_identity = _identity(self.root)
            marker = self.root / _MARKER
            if marker.exists() or marker.is_symlink():
                _safe_route(marker)
                if marker.read_bytes() != b'{"format":1}\n':
                    raise WorkspaceError("WORKSPACE_ROOT_INVALID")
            else:
                if any(self.root.iterdir()):
                    raise WorkspaceError("WORKSPACE_ROOT_NOT_EMPTY")
                with marker.open("xb") as stream:
                    stream.write(b'{"format":1}\n')
            for name in ("candidates", "records", "releases"):
                path = self.root / name
                path.mkdir(exist_ok=True)
                _safe_route(path, directory=True)
            self._directory_identities = {
                name: _identity(self.root / name) for name in ("candidates", "records", "releases")
            }
            self._database = self.root / "records" / "workspace.sqlite3"
            with self._transaction() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS candidates "
                    "(id TEXT PRIMARY KEY, extension_id TEXT NOT NULL, version TEXT NOT NULL, "
                    "baseline TEXT NOT NULL)"
                )
        except WorkspaceError:
            raise
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            raise WorkspaceError("WORKSPACE_ROOT_INVALID") from None

    def _assert_root(self) -> None:
        _safe_route(self.root, directory=True)
        if _identity(self.root) != self._root_identity:
            raise WorkspaceError("WORKSPACE_ROOT_CHANGED")
        marker = self.root / _MARKER
        _safe_route(marker)
        if marker.read_bytes() != b'{"format":1}\n':
            raise WorkspaceError("WORKSPACE_ROOT_CHANGED")
        for name, expected in self._directory_identities.items():
            directory = self.root / name
            _safe_route(directory, directory=True)
            if _identity(directory) != expected:
                raise WorkspaceError("WORKSPACE_ROOT_CHANGED")
        if self._database.exists() or self._database.is_symlink():
            _safe_route(self._database)
            if not self._database.is_file():
                raise WorkspaceError("WORKSPACE_PATH_INVALID")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            self._assert_root()
            connection = sqlite3.connect(self._database, timeout=5)
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            self._assert_root()
            connection.commit()
        except WorkspaceError:
            if connection is not None:
                connection.rollback()
            raise
        except ValidationError as error:
            if connection is not None:
                connection.rollback()
            raise WorkspaceError(error.code) from None
        except (OSError, RuntimeError, ValueError, sqlite3.Error, UnicodeError):
            if connection is not None:
                connection.rollback()
            raise WorkspaceError("WORKSPACE_OPERATION_FAILED") from None
        finally:
            if connection is not None:
                connection.close()

    def _record(self, connection: sqlite3.Connection, candidate_id: str) -> dict[str, Any]:
        if not isinstance(candidate_id, str) or not _CANDIDATE.fullmatch(candidate_id):
            raise WorkspaceError("CANDIDATE_NOT_FOUND")
        row = connection.execute(
            "SELECT extension_id, version, baseline FROM candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise WorkspaceError("CANDIDATE_NOT_FOUND")
        return {
            "candidate_id": candidate_id,
            "extension_id": row[0],
            "version": row[1],
            "baseline": json.loads(row[2]),
        }

    def _candidate_path(self, candidate_id: str) -> Path:
        path = self.root / "candidates" / candidate_id
        _safe_route(path, directory=True)
        return path

    def candidate_path(self, candidate_id: str) -> Path:
        # Internal local-management path, never an Agent response.
        with self._transaction() as connection:
            self._record(connection, candidate_id)
            return self._candidate_path(candidate_id)

    def _file(self, candidate: Path, value: str, *, create: bool = False) -> Path:
        parts = _parts(value)
        current = candidate
        for index, part in enumerate(parts):
            if current.exists():
                _safe_route(current, directory=True)
                aliases = [
                    entry.name
                    for entry in current.iterdir()
                    if entry.name.casefold() == part.casefold()
                ]
                if aliases and aliases != [part]:
                    raise WorkspaceError("WORKSPACE_PATH_ALIAS")
            current = current / part
            if current.exists() or current.is_symlink():
                _safe_route(current)
                if index < len(parts) - 1 and not current.is_dir():
                    raise WorkspaceError("WORKSPACE_PATH_INVALID")
            elif not create:
                raise WorkspaceError("WORKSPACE_FILE_NOT_FOUND")
        if not current.is_relative_to(candidate):
            raise WorkspaceError("WORKSPACE_PATH_INVALID")
        return current

    @staticmethod
    def _read(path: Path) -> str:
        _safe_route(path)
        if not path.is_file():
            raise WorkspaceError("WORKSPACE_FILE_NOT_FOUND")
        before = path.stat()
        if before.st_size > MAX_FILE_BYTES:
            raise WorkspaceError("WORKSPACE_FILE_TOO_LARGE")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise WorkspaceError("WORKSPACE_CHANGED")
            raw = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
        _safe_route(path)
        current = path.stat()
        if (
            len(raw) > MAX_FILE_BYTES
            or _file_version(current) != _file_version(opened)
            or _file_version(after) != _file_version(opened)
        ):
            raise WorkspaceError("WORKSPACE_CHANGED")
        content = raw.decode("utf-8")
        if _encode(content) != raw:
            raise WorkspaceError("WORKSPACE_TEXT_REQUIRED")
        return content

    def create(self, extension_id: str, version: str = "0.1.0") -> str:
        if (
            not isinstance(extension_id, str)
            or len(extension_id) > 64
            or not _ID.fullmatch(extension_id)
            or not isinstance(version, str)
            or len(version) > 64
            or not _VERSION.fullmatch(version)
        ):
            raise WorkspaceError("CANDIDATE_IDENTITY_INVALID")
        files = _skeleton(extension_id, version)
        with self._transaction() as connection:
            candidate_id = "cand_" + uuid.uuid4().hex
            candidate = self.root / "candidates" / candidate_id
            candidate.mkdir()
            for name, content in files.items():
                path = self._file(candidate, name, create=True)
                path.parent.mkdir(parents=True, exist_ok=True)
                _safe_route(path.parent, directory=True)
                with path.open("xb") as stream:
                    stream.write(_encode(content))
            candidate_files(candidate)
            connection.execute(
                "INSERT INTO candidates(id,extension_id,version,baseline) VALUES(?,?,?,?)",
                (
                    candidate_id,
                    extension_id,
                    version,
                    json.dumps(files, ensure_ascii=False, sort_keys=True),
                ),
            )
            return candidate_id

    def list_candidates(self) -> list[dict[str, str]]:
        with self._transaction() as connection:
            return [
                {"candidate_id": row[0], "extension_id": row[1], "version": row[2]}
                for row in connection.execute(
                    "SELECT id,extension_id,version FROM candidates ORDER BY id"
                )
            ]

    def tree(self, candidate_id: str) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            self._record(connection, candidate_id)
            return candidate_files(self._candidate_path(candidate_id))

    def read(self, candidate_id: str, path: str) -> str:
        with self._transaction() as connection:
            self._record(connection, candidate_id)
            return self._read(self._file(self._candidate_path(candidate_id), path))

    def write(
        self, candidate_id: str, path: str, content: str, expected_sha256: str | None = None
    ) -> dict[str, Any]:
        encoded = _encode(content)
        with self._transaction() as connection:
            record = self._record(connection, candidate_id)
            candidate = self._candidate_path(candidate_id)
            target = self._file(candidate, path, create=True)
            files = candidate_files(candidate)
            normalized = target.relative_to(candidate).as_posix()
            existing = next((item for item in files if item["path"] == normalized), None)
            if existing is None:
                if expected_sha256 is not None or target.exists():
                    raise WorkspaceError("WORKSPACE_VERSION_CONFLICT")
            elif expected_sha256 is None or expected_sha256 != existing["sha256"]:
                raise WorkspaceError("WORKSPACE_VERSION_CONFLICT")
            total = sum(item["size_bytes"] for item in files)
            if (
                total - (existing["size_bytes"] if existing else 0) + len(encoded)
                > MAX_CANDIDATE_BYTES
            ):
                raise WorkspaceError("WORKSPACE_CANDIDATE_TOO_LARGE")
            if existing is None and len(files) >= MAX_FILES:
                raise WorkspaceError("WORKSPACE_TOO_MANY_FILES")
            if normalized == "extension.toml":
                self._manifest_identity(content, record)
            target.parent.mkdir(parents=True, exist_ok=True)
            _safe_route(target.parent, directory=True)
            temporary = self.root / "records" / ("write-" + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("xb") as stream:
                    stream.write(encoded)
                self._assert_root()
                self._file(candidate, path, create=True)
                if existing is not None:
                    actual = hashlib.sha256(self._read(target).encode("utf-8")).hexdigest()
                    if actual != expected_sha256:
                        raise WorkspaceError("WORKSPACE_VERSION_CONFLICT")
                elif target.exists():
                    raise WorkspaceError("WORKSPACE_VERSION_CONFLICT")
                os.replace(temporary, target)
                _safe_route(target)
            finally:
                temporary.unlink(missing_ok=True)
            return {
                "path": normalized,
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "size_bytes": len(encoded),
            }

    @staticmethod
    def _manifest_identity(content: str, record: dict[str, Any]) -> None:
        try:
            manifest = tomllib.loads(content)
        except tomllib.TOMLDecodeError:
            raise WorkspaceError("CANDIDATE_MANIFEST_INVALID") from None
        if (
            manifest.get("id") != record["extension_id"]
            or manifest.get("version") != record["version"]
        ):
            raise WorkspaceError("CANDIDATE_MANIFEST_IDENTITY_CHANGED")

    def review(self, candidate_id: str) -> dict[str, Any]:
        with self._transaction() as connection:
            record = self._record(connection, candidate_id)
            candidate = self._candidate_path(candidate_id)
            files = candidate_files(candidate)
            manifest = self._read(self._file(candidate, "extension.toml"))
            self._manifest_identity(manifest, record)
            baseline = cast(dict[str, str], record["baseline"])
            current = {
                item["path"]: self._read(self._file(candidate, item["path"])) for item in files
            }
            changes = []
            for name in sorted(set(baseline) | set(current)):
                if baseline.get(name) != current.get(name):
                    diff = "".join(
                        difflib.unified_diff(
                            baseline.get(name, "").splitlines(keepends=True),
                            current.get(name, "").splitlines(keepends=True),
                            fromfile="baseline/" + name,
                            tofile="candidate/" + name,
                        )
                    )
                    changes.append({"path": name, "diff": diff})
            digest = candidate_digest(candidate)
            if candidate_files(candidate) != files:
                raise WorkspaceError("WORKSPACE_CHANGED")
            return {
                "candidate_id": candidate_id,
                "extension_id": record["extension_id"],
                "version": record["version"],
                "candidate_hash": digest,
                "files": files,
                "manifest": manifest,
                "diff": changes,
            }

    def export(self, candidate_id: str, expected_digest: str) -> Path:
        if not isinstance(expected_digest, str) or not _HASH.fullmatch(expected_digest):
            raise WorkspaceError("CANDIDATE_DIGEST_REQUIRED")
        with self._transaction() as connection:
            record = self._record(connection, candidate_id)
            candidate = self._candidate_path(candidate_id)
            self._manifest_identity(self._read(self._file(candidate, "extension.toml")), record)
            if candidate_digest(candidate) != expected_digest:
                raise WorkspaceError("CANDIDATE_CHANGED")
            release = self.root / "releases" / (candidate_id + "-" + expected_digest)
            destination = release / "payload"
            if release.exists():
                _safe_route(release, directory=True)
                _safe_route(destination, directory=True)
                if candidate_digest(destination) != expected_digest:
                    raise WorkspaceError("CANDIDATE_CHANGED")
                return destination
            release.mkdir()
            result = snapshot_candidate(candidate, destination, expected_digest)
            self._assert_root()
            if candidate_digest(candidate) != expected_digest:
                raise WorkspaceError("CANDIDATE_CHANGED")
            return result


def _skeleton(extension_id: str, version: str) -> dict[str, str]:
    package = re.sub(r"[^a-z0-9_]", "_", extension_id)
    tool_id = extension_id + ".ping"
    manifest = _manifest(extension_id, package, tool_id).replace(
        'version = "0.1.0"', f'version = "{version}"', 1
    )
    pyproject = _pyproject(extension_id, package).replace(
        'version = "0.1.0"', f'version = "{version}"', 1
    )
    return {
        "extension.toml": manifest,
        "pyproject.toml": pyproject,
        "requirements.lock": "# SDK is supplied by the host; pin all additional dependencies.\n",
        "README.md": _readme(extension_id),
        f"src/{package}/__init__.py": "",
        f"src/{package}/worker.py": _worker(extension_id, tool_id),
        "schemas/ping-input.json": _json_schema(
            {"type": "object", "properties": {}, "additionalProperties": False}
        ),
        "schemas/ping-output.json": _json_schema(
            {
                "type": "object",
                "properties": {"status": {"type": "string", "const": "ok"}},
                "required": ["status"],
                "additionalProperties": False,
            }
        ),
        "tests/test_basic.py": _test_source(package, extension_id, version, tool_id),
    }


# Standalone SDK templates copied from the project's scaffold at development time.


def _manifest(extension_id: str, package: str, tool_id: str) -> str:
    return f'''manifest_version = "1"
id = "{extension_id}"
name = "{extension_id}"
version = "0.1.0"
core_api = ">=1,<2"
python = ">=3.12,<3.14"
entrypoint = "{package}.worker:create_extension"
dependency_lock = "requirements.lock"
state_schema_version = 1
healthcheck = "system.health"

[[tools]]
id = "{tool_id}"
risk = "READ"
input_schema = "schemas/ping-input.json"
output_schema = "schemas/ping-output.json"

[capabilities]
required = []
optional = []
'''


def _pyproject(extension_id: str, package: str) -> str:
    distribution = extension_id.replace(".", "-").replace("_", "-")
    return f'''[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{distribution}"
version = "0.1.0"
requires-python = ">=3.12,<3.14"
dependencies = ["personal-assistant-extension-sdk==1.0.0"]

[tool.setuptools.packages.find]
where = ["src"]
include = ["{package}*"]
'''


def _worker(extension_id: str, tool_id: str) -> str:
    return f'''from __future__ import annotations

from personal_assistant_sdk import (
    PROTOCOL_VERSION,
    DrainReport,
    ExtensionInfo,
    HealthReport,
    InvocationContext,
    Outcome,
    RiskLevel,
    RuntimeContext,
    ToolDescriptor,
    ToolResult,
)
from personal_assistant_sdk.worker import run_stdio_worker


class ExtensionImplementation:
    def __init__(self) -> None:
        self.runtime: RuntimeContext | None = None

    async def initialize(self, runtime: RuntimeContext) -> ExtensionInfo:
        self.runtime = runtime
        return ExtensionInfo(
            id=runtime.extension_id,
            version=runtime.extension_version,
            protocol_version=PROTOCOL_VERSION,
            slots=("ToolProvider",),
            schema_hash=runtime.manifest_schema_hash,
        )

    async def health(self) -> HealthReport:
        return HealthReport(self.runtime is not None, "ready")

    async def drain(self, deadline: float) -> DrainReport:
        del deadline
        return DrainReport(True, 0)

    async def shutdown(self) -> None:
        self.runtime = None

    def tools(self) -> tuple[ToolDescriptor, ...]:
        return (
            ToolDescriptor(
                id="{tool_id}",
                risk=RiskLevel.READ,
                input_schema={{"type": "object", "additionalProperties": False}},
                output_schema={{
                    "type": "object",
                    "properties": {{"status": {{"type": "string", "const": "ok"}}}},
                    "required": ["status"],
                    "additionalProperties": False,
                }},
            ),
        )

    async def invoke(
        self, tool_id: str, arguments: dict, context: InvocationContext
    ) -> ToolResult:
        del arguments, context
        if tool_id != "{tool_id}":
            raise ValueError("unknown tool")
        return ToolResult(Outcome.SUCCEEDED, {{"status": "ok"}})


def create_extension() -> ExtensionImplementation:
    return ExtensionImplementation()


if __name__ == "__main__":
    run_stdio_worker(create_extension)
'''


def _readme(extension_id: str) -> str:
    return f"""# {extension_id}

由 `assistantctl extension scaffold` 生成。只依赖公开 SDK；不得导入宿主 core。

在实现真实能力前：更新 Manifest 风险与 Schema，添加契约测试。
所有副作用仍须由宿主 Tool Gateway 管理。
"""


def _json_schema(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _test_source(package: str, extension_id: str, version: str, tool_id: str) -> str:
    return f"""import unittest

from personal_assistant_sdk import InvocationContext, Outcome, RuntimeContext
from {package}.worker import create_extension


class GeneratedContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_sdk_ping_lifecycle(self):
        worker = create_extension()
        try:
            identity = await worker.initialize(
                RuntimeContext("1", "{extension_id}", "{version}", "fixture")
            )
            self.assertEqual("{extension_id}", identity.id)
            self.assertTrue((await worker.health()).healthy)
            tools = worker.tools()
            self.assertEqual(["{tool_id}"], [tool.id for tool in tools])
            result = await worker.invoke(
                "{tool_id}", {{}},
                InvocationContext("fixture", "run", "2030-01-01T00:00:00Z", "key"),
            )
            self.assertIs(Outcome.SUCCEEDED, result.outcome)
            self.assertEqual({{"status": "ok"}}, dict(result.output))
        finally:
            await worker.shutdown()
        self.assertFalse((await worker.health()).healthy)
"""
