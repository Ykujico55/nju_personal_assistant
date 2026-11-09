"""One owner-managed workspace, projected into opt-in extension runtimes.

Plugin drafts never receive copies of roots. The existing durable configuration
store supplies CAS apply, atomic snapshots and live replica acknowledgements.
This is an authorization contract for trusted extensions, not an OS sandbox.
"""
from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from personal_assistant.core.extensions.config import (
    ExtensionConfigError,
    config_digest,
    validate_extension_config,
)

from .config_store import FileExtensionConfigStore

_WORKSPACE = "instance.workspace"
WORKSPACE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "workspace_id": {"type": "string", "enum": ["instance"]},
        "roots": {
            "type": "array", "maxItems": 16,
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"},
                    "label": {"type": "string", "minLength": 1, "maxLength": 120},
                    "path": {"type": "string", "minLength": 1, "maxLength": 1024},
                    "mode": {"type": "string", "enum": ["read_only", "read_write"]},
                },
                "required": ["key", "label", "path", "mode"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["workspace_id", "roots"],
    "additionalProperties": False,
}
_DENIED = {".git", ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".codex", ".env"}


def _binding(config: Mapping[str, Any]) -> bool:
    if "workspace_binding" not in config:
        return False
    if config["workspace_binding"] != "instance" or "roots" in config:
        raise ExtensionConfigError("WORKSPACE_BINDING_INVALID")
    return True


def _state(target: Mapping[str, Any], runtimes: list[Mapping[str, Any]]) -> str:
    if not runtimes:
        return "PENDING"
    if any(r["generation"] == target["generation"] and r["state"] == "FAILED" for r in runtimes):
        return "FAILED"
    if all(r["generation"] == target["generation"] and r["loaded_digest"] == target["digest"]
           and r["state"] == "RUNNING" for r in runtimes):
        return "APPLIED"
    return "APPLYING"


class WorkspaceExtensionConfigStore:
    def __init__(
        self, base: FileExtensionConfigStore, workspace_root: Path,
        *, protected_paths: tuple[Path, ...] = (),
    ) -> None:
        self._base = base
        self._workspace = FileExtensionConfigStore(workspace_root)
        self._protected = tuple(Path(p).resolve() for p in (
            base.root, workspace_root, *protected_paths,
        ))

    async def get(self, extension_id: str) -> Mapping[str, Any]:
        return await self._base.get(extension_id)

    async def save(self, extension_id: str, config: Mapping[str, Any]) -> None:
        _binding(config)
        await self._base.save(extension_id, config)

    async def get_applied(self, extension_id: str) -> Mapping[str, Any]:
        base = await self._base.get_applied(extension_id)
        if not _binding(base["config"]):
            return base
        active = await self._workspace.get_applied(_WORKSPACE)
        document = active["config"]
        if document:
            document = validate_extension_config(WORKSPACE_SCHEMA, document)
        workspace = {
            "workspace_id": "instance",
            "revision": config_digest({"generation": active["generation"]})[7:],
            "roots": document.get("roots", []),
        }
        generation = config_digest(
            {"plugin": base["generation"], "workspace": active["generation"]}
        )
        return {
            "config": base["config"], "workspace": workspace,
            "digest": config_digest({"config": base["config"], "workspace": workspace}),
            "generation": generation,
        }

    async def apply(self, extension_id: str, expected_digest: str) -> Mapping[str, Any]:
        _binding(await self._base.get(extension_id))
        await self._base.apply(extension_id, expected_digest)
        return await self.status(extension_id)

    async def status(self, extension_id: str) -> Mapping[str, Any]:
        status = dict(await self._base.status(extension_id))
        base = await self._base.get_applied(extension_id)
        target = await self.get_applied(extension_id)
        if "workspace" not in target:
            return status
        status["desired_digest"], status["generation"] = target["digest"], target["generation"]
        status["state"] = (
            "PENDING" if status["saved_digest"] != base["digest"]
            else _state(target, status["runtimes"])
        )
        status["workspace_revision"] = target["workspace"]["revision"]
        return status

    async def report_runtime(
        self, extension_id: str, instance_id: str, *, loaded_digest: str | None,
        generation: str | None, state: str, error_code: str | None = None,
    ) -> None:
        await self._base.report_runtime(
            extension_id, instance_id, loaded_digest=loaded_digest, generation=generation,
            state=state, error_code=error_code,
        )

    async def release_runtime(self, instance_id: str) -> None:
        await self._base.release_runtime(instance_id)

    async def get_workspace(self) -> Mapping[str, Any]:
        # A confirmation must describe the exact draft named by its apply CAS.
        # Each read is atomic, but another host may save between the two awaits.
        # Retry that pair, never attach a newer digest to an older document.
        for _ in range(3):
            draft = dict(await self._workspace.get(_WORKSPACE))
            status = dict(await self._workspace.status(_WORKSPACE))
            if config_digest(draft) == status["saved_digest"]:
                break
        else:
            raise ExtensionConfigError("CONFIG_REVISION_CONFLICT")
        consumers = []
        for extension_id in await self._base.list_configured_ids():
            active = await self._base.get_applied(extension_id)
            saved = await self._base.get(extension_id)
            if _binding(active["config"]) or _binding(saved):
                detail = await self.status(extension_id)
                consumers.append({"extension_id": extension_id, **detail})
        if status["saved_digest"] == status["desired_digest"] and consumers:
            states = {item["state"] for item in consumers}
            status["state"] = (
                "FAILED" if "FAILED" in states else
                "APPLIED" if states == {"APPLIED"} else
                "APPLYING" if "APPLYING" in states else "PENDING"
            )
        # Consumer acknowledgements are read separately. A concurrent owner apply
        # must not let acknowledgements for B certify the earlier snapshot A.
        final = await self._workspace.status(_WORKSPACE)
        if any(final[key] != status[key] for key in ("generation", "saved_digest")):
            status["state"] = "APPLYING"
            status["refresh_required"] = True
        status["consumers"] = consumers
        return {
            "config": draft,
            "config_schema": WORKSPACE_SCHEMA,
            "application": status,
        }

    async def save_workspace(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = await asyncio.to_thread(self._validate_workspace, config)
        await self._workspace.save(_WORKSPACE, normalized)
        return await self.get_workspace()

    async def apply_workspace(self, expected_digest: str) -> Mapping[str, Any]:
        saved = await self._workspace.get(_WORKSPACE)
        if config_digest(saved) != expected_digest:
            raise ExtensionConfigError("CONFIG_REVISION_CONFLICT")
        # Revalidate availability at application, not just at editing time.
        await asyncio.to_thread(self._validate_workspace, saved)
        await self._workspace.apply(_WORKSPACE, expected_digest)
        return await self.get_workspace()

    def _validate_workspace(self, config: Mapping[str, Any]) -> dict[str, Any]:
        normalized = validate_extension_config(WORKSPACE_SCHEMA, config)
        paths: list[Path] = []
        keys: set[str] = set()
        try:
            for item in normalized["roots"]:
                key, label, raw = item["key"], item["label"], item["path"]
                if key.casefold() in keys or any(ord(c) < 32 for c in label + raw):
                    raise ValueError
                keys.add(key.casefold())
                path = Path(raw)
                if (not path.is_absolute() or raw != raw.strip() or ".." in path.parts
                        or raw.startswith(("\\\\", "//")) or path == Path(path.anchor)):
                    raise ValueError
                for component in (*reversed(path.parents), path):
                    value = component.lstat()
                    if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & (
                        stat.FILE_ATTRIBUTE_REPARSE_POINT
                    ):
                        raise ValueError
                resolved = path.resolve(strict=True)
                if not resolved.is_dir() or any(
                    part.casefold() in _DENIED or part.casefold().startswith(".env.")
                    for part in resolved.parts
                ):
                    raise ValueError
                if os.path.normcase(os.path.normpath(raw)) != os.path.normcase(str(resolved)):
                    raise ValueError  # Reject aliases, including Windows short names.
                if any(resolved.is_relative_to(p) or p.is_relative_to(resolved)
                       for p in (*paths, *self._protected)):
                    raise ValueError
                paths.append(resolved)
                item["path"] = str(resolved)
        except (OSError, RuntimeError, ValueError):
            raise ExtensionConfigError("WORKSPACE_ROOT_INVALID") from None
        return normalized
