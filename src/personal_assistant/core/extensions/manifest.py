"""Pure-data ``extension.toml`` parsing and validation.

This module never imports, builds, installs, or starts extension code.  It is safe
to run before the user has trusted an artifact.
"""

from __future__ import annotations

import hashlib
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ManifestValidationError

_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_ENTRYPOINT_RE = re.compile(
    r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$"
)
_SLOT_KEYS = {
    "event_sources": "EventSource",
    "context_providers": "ContextProvider",
    "workflows": "WorkflowProvider",
    "schedules": "ScheduleProvider",
    "notifications": "NotificationProvider",
    "migrations": "MigrationProvider",
    "forms": "FormSchemaProvider",
}
_RISK_LEVELS = {"READ", "INTERNAL_WRITE", "EXTERNAL_WRITE", "PROHIBITED"}

# Staging, hashing and installation must all use exactly this file set: an
# ignored path never reaches the staged tree, so it can neither change the
# confirmed hash nor end up in the installed payload.
IGNORED_ARTIFACT_PARTS = frozenset({".git", ".venv", "__pycache__", ".pytest_cache"})
_IGNORED_ARTIFACT_PARTS = IGNORED_ARTIFACT_PARTS

_EXACT_VERSION_RE = re.compile(
    r"^[0-9]+(?:\.[0-9]+)*(?:[A-Za-z][A-Za-z0-9.]*)?(?:\+[A-Za-z0-9.]+)?$"
)
_PINNED_REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9._,-]+\])?==([^==;,\s<>!~*]+)$"
)
_SHA256_HASH_RE = re.compile(r"^--hash=sha256:([0-9a-f]{64})$")


@dataclass(frozen=True, slots=True)
class ManifestTool:
    id: str
    risk: str
    input_schema: str
    output_schema: str


@dataclass(frozen=True, slots=True)
class DeclaredCapabilities:
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    root: Path
    manifest_version: str
    id: str
    name: str
    version: str
    core_api: str
    python: str
    entrypoint: str
    dependency_lock: str
    config_schema: str | None
    state_schema_version: int
    healthcheck: str
    tools: tuple[ManifestTool, ...] = ()
    event_sources: tuple[str, ...] = ()
    context_providers: tuple[str, ...] = ()
    workflows: tuple[str, ...] = ()
    schedules: tuple[str, ...] = ()
    notifications: tuple[str, ...] = ()
    migrations: tuple[str, ...] = ()
    forms: tuple[str, ...] = ()
    capabilities: DeclaredCapabilities = field(default_factory=DeclaredCapabilities)

    @property
    def slots(self) -> Mapping[str, tuple[str, ...]]:
        values: dict[str, tuple[str, ...]] = {
            "ToolProvider": tuple(tool.id for tool in self.tools),
        }
        for key, label in _SLOT_KEYS.items():
            declared = tuple(getattr(self, key))
            if declared:
                values[label] = declared
        return values

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(capability for items in self.slots.values() for capability in items)

    @property
    def module_name(self) -> str:
        return self.entrypoint.partition(":")[0]

    @property
    def callable_name(self) -> str:
        return self.entrypoint.partition(":")[2]


class ManifestParser:
    """Parse and validate a staged extension using Python's standard library."""

    def __init__(
        self,
        *,
        supported_manifest_version: str = "1",
        core_api_version: tuple[int, int] = (1, 0),
        python_version: tuple[int, int] | None = None,
    ) -> None:
        self.supported_manifest_version = supported_manifest_version
        self.core_api_version = core_api_version
        self.python_version = python_version or (sys.version_info.major, sys.version_info.minor)

    def parse(self, extension_root: str | Path) -> ExtensionManifest:
        root = Path(extension_root).resolve()
        manifest_path = root / "extension.toml"
        if not manifest_path.is_file():
            raise ManifestValidationError("extension.toml is required at the extension root")
        try:
            with manifest_path.open("rb") as stream:
                raw = tomllib.load(stream)
        except tomllib.TOMLDecodeError as exc:
            raise ManifestValidationError(f"invalid extension.toml: {exc}") from exc

        allowed = {
            "manifest_version",
            "id",
            "name",
            "version",
            "core_api",
            "python",
            "entrypoint",
            "dependency_lock",
            "config_schema",
            "state_schema_version",
            "healthcheck",
            "tools",
            "capabilities",
            *_SLOT_KEYS,
        }
        unknown = set(raw) - allowed
        if unknown:
            raise ManifestValidationError(f"unknown manifest keys: {sorted(unknown)}")

        manifest_version = _required_string(raw, "manifest_version")
        if manifest_version != self.supported_manifest_version:
            raise ManifestValidationError(
                f"unsupported manifest_version {manifest_version!r}"
            )
        extension_id = _required_string(raw, "id")
        if not _ID_RE.fullmatch(extension_id):
            raise ManifestValidationError("id must be a namespaced lowercase identifier")
        version = _required_string(raw, "version")
        if not _SEMVER_RE.fullmatch(version):
            raise ManifestValidationError("version must be semantic versioning")
        entrypoint = _required_string(raw, "entrypoint")
        if not _ENTRYPOINT_RE.fullmatch(entrypoint):
            raise ManifestValidationError("entrypoint must have module.path:callable form")
        core_api = _required_string(raw, "core_api")
        python_spec = _required_string(raw, "python")
        _validate_range(core_api, self.core_api_version, "core_api")
        _validate_range(python_spec, self.python_version, "python")

        dependency_lock = _required_string(raw, "dependency_lock")
        lock_path = _require_safe_file(root, dependency_lock, "dependency_lock")
        _validate_lockfile(lock_path)
        config_schema_value = raw.get("config_schema")
        if config_schema_value is not None:
            if not isinstance(config_schema_value, str) or not config_schema_value:
                raise ManifestValidationError("config_schema must be a non-empty path")
            _require_safe_file(root, config_schema_value, "config_schema")

        state_schema_version = raw.get("state_schema_version")
        if not isinstance(state_schema_version, int) or state_schema_version < 0:
            raise ManifestValidationError("state_schema_version must be a non-negative integer")

        tools = _parse_tools(root, raw.get("tools", []))
        slot_values = {
            key: _identifier_list(raw.get(key, []), key) for key in _SLOT_KEYS
        }
        capability_ids = [tool.id for tool in tools]
        capability_ids.extend(item for values in slot_values.values() for item in values)
        duplicates = _duplicates(capability_ids)
        if duplicates:
            raise ManifestValidationError(
                f"capability ids must be globally unique in an extension: {duplicates}"
            )

        capabilities_raw = raw.get("capabilities", {})
        if not isinstance(capabilities_raw, dict):
            raise ManifestValidationError("capabilities must be a table")
        unknown_capabilities = set(capabilities_raw) - {"required", "optional"}
        if unknown_capabilities:
            raise ManifestValidationError(
                f"unknown capabilities keys: {sorted(unknown_capabilities)}"
            )
        required_capabilities = _identifier_list(
            capabilities_raw.get("required", []), "capabilities.required"
        )
        optional_capabilities = _identifier_list(
            capabilities_raw.get("optional", []), "capabilities.optional"
        )
        overlap = set(required_capabilities) & set(optional_capabilities)
        if overlap:
            raise ManifestValidationError(
                f"capabilities cannot be both required and optional: {sorted(overlap)}"
            )

        return ExtensionManifest(
            root=root,
            manifest_version=manifest_version,
            id=extension_id,
            name=_required_string(raw, "name"),
            version=version,
            core_api=core_api,
            python=python_spec,
            entrypoint=entrypoint,
            dependency_lock=dependency_lock,
            config_schema=config_schema_value,
            state_schema_version=state_schema_version,
            healthcheck=_required_string(raw, "healthcheck"),
            tools=tools,
            capabilities=DeclaredCapabilities(
                required=required_capabilities,
                optional=optional_capabilities,
            ),
            **slot_values,
        )


def compute_artifact_hash(extension_root: str | Path) -> str:
    """Hash an artifact tree deterministically without executing it."""

    root = Path(extension_root).resolve()
    if not root.is_dir():
        raise ManifestValidationError("extension artifact root must be a directory")
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in _IGNORED_ARTIFACT_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ManifestValidationError("extension artifacts may not contain symlinks")
        if path.is_file():
            files.append(path)
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative_bytes = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        size = path.stat().st_size
        digest.update(size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def compute_schema_hash(manifest: ExtensionManifest) -> str:
    digest = hashlib.sha256()
    references: list[str] = []
    if manifest.config_schema:
        references.append(manifest.config_schema)
    for tool in manifest.tools:
        references.extend((tool.input_schema, tool.output_schema))
    for reference in sorted(set(references)):
        data = (manifest.root / reference).read_bytes()
        digest.update(reference.encode("utf-8"))
        digest.update(data)
    return f"sha256:{digest.hexdigest()}"


def discover_manifests(
    parent: str | Path, parser: ManifestParser | None = None
) -> tuple[ExtensionManifest, ...]:
    parser = parser or ManifestParser()
    parent_path = Path(parent)
    if not parent_path.is_dir():
        return ()
    discovered: list[ExtensionManifest] = []
    for manifest_path in sorted(parent_path.glob("*/extension.toml")):
        discovered.append(parser.parse(manifest_path.parent))
    return tuple(discovered)


def _parse_tools(root: Path, raw_tools: Any) -> tuple[ManifestTool, ...]:
    if not isinstance(raw_tools, list):
        raise ManifestValidationError("tools must be an array of tables")
    tools: list[ManifestTool] = []
    for index, item in enumerate(raw_tools):
        if not isinstance(item, dict):
            raise ManifestValidationError(f"tools[{index}] must be a table")
        unknown = set(item) - {"id", "risk", "input_schema", "output_schema"}
        if unknown:
            raise ManifestValidationError(f"unknown tools[{index}] keys: {sorted(unknown)}")
        tool_id = _required_string(item, "id", prefix=f"tools[{index}].")
        if not _ID_RE.fullmatch(tool_id):
            raise ManifestValidationError(f"invalid tool id: {tool_id!r}")
        risk = _required_string(item, "risk", prefix=f"tools[{index}].")
        if risk not in _RISK_LEVELS:
            raise ManifestValidationError(f"unsupported risk level: {risk!r}")
        input_schema = _required_string(item, "input_schema", prefix=f"tools[{index}].")
        output_schema = _required_string(item, "output_schema", prefix=f"tools[{index}].")
        _require_safe_file(root, input_schema, f"tools[{index}].input_schema")
        _require_safe_file(root, output_schema, f"tools[{index}].output_schema")
        tools.append(ManifestTool(tool_id, risk, input_schema, output_schema))
    return tuple(tools)


def _required_string(raw: Mapping[str, Any], key: str, *, prefix: str = "") -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestValidationError(f"{prefix}{key} must be a non-empty string")
    return value.strip()


def _identifier_list(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ManifestValidationError(f"{field_name} must be an array of strings")
    normalized = tuple(item.strip() for item in value)
    if any(not _ID_RE.fullmatch(item) for item in normalized):
        raise ManifestValidationError(f"{field_name} contains an invalid identifier")
    duplicates = _duplicates(normalized)
    if duplicates:
        raise ManifestValidationError(f"{field_name} contains duplicates: {duplicates}")
    return normalized


def _validate_lockfile(lock_path: Path) -> None:
    """Static lockfile validation; this is data-only and never executes pip.

    The lock must be a complete, hash-checked, exactly-pinned set: every
    requirement line is ``name==exact.version`` (optionally with extras) followed
    by at least one ``--hash=sha256:<64 hex>`` token.  Wildcards, ranges,
    environment markers, options, editable installs and URL/VCS requirements are
    rejected so pip can never resolve or fetch anything that is not pinned.
    """

    text = lock_path.read_text("utf-8")
    logical_lines: list[tuple[int, str]] = []
    buffer = ""
    start_line = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not buffer:
            start_line = number
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        buffer += stripped
        logical_lines.append((start_line, buffer))
        buffer = ""
    if buffer:
        logical_lines.append((start_line, buffer))

    for number, line in logical_lines:
        if not line or line.startswith("#"):
            continue
        if ";" in line or "://" in line or line.startswith("-"):
            raise ManifestValidationError(
                f"dependency_lock line {number} is not a plain pinned requirement"
            )
        tokens = line.split()
        requirement = tokens[0]
        hashes = tokens[1:]
        match = _PINNED_REQUIREMENT_RE.fullmatch(requirement)
        if match is None or not _EXACT_VERSION_RE.fullmatch(match.group(3)):
            raise ManifestValidationError(
                f"dependency_lock line {number} must pin one exact version with '=='"
            )
        if not hashes:
            raise ManifestValidationError(
                f"dependency_lock line {number} must declare --hash=sha256:..."
            )
        for token in hashes:
            if _SHA256_HASH_RE.fullmatch(token) is None:
                raise ManifestValidationError(
                    f"dependency_lock line {number} has an unsupported hash token"
                )


def _require_safe_file(root: Path, reference: str, field_name: str) -> Path:
    candidate = (root / reference).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ManifestValidationError(f"{field_name} escapes the extension root") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise ManifestValidationError(f"{field_name} does not name a regular file")
    return candidate


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for value in values:
        if value in seen:
            repeated.add(value)
        seen.add(value)
    return sorted(repeated)


def _validate_range(spec: str, actual: tuple[int, int], field_name: str) -> None:
    """Validate the intentionally small ``>=X[.Y],<Z[.Y]`` contract subset."""

    constraints = [item.strip() for item in spec.split(",") if item.strip()]
    if not constraints:
        raise ManifestValidationError(f"{field_name} range is empty")
    for constraint in constraints:
        match = re.fullmatch(r"(>=|>|<=|<|==)(\d+)(?:\.(\d+))?", constraint)
        if not match:
            raise ManifestValidationError(
                f"{field_name} uses unsupported range syntax: {constraint!r}"
            )
        operator, major, minor = match.groups()
        target = (int(major), int(minor or 0))
        accepted = {
            ">=": actual >= target,
            ">": actual > target,
            "<=": actual <= target,
            "<": actual < target,
            "==": actual == target,
        }[operator]
        if not accepted:
            rendered = f"{actual[0]}.{actual[1]}"
            raise ManifestValidationError(
                f"{field_name} {rendered} does not satisfy {spec!r}"
            )
