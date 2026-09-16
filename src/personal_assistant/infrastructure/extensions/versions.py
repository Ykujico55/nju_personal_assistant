"""Retained-version catalogs and the side-by-side upgrade/rollback operator.

Rollback is allowed only to a retained version whose declared data schema can
still read what the active version wrote, and only while that version's installed
files are still present.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from personal_assistant.core.extensions.errors import ExtensionOperationError
from personal_assistant.core.extensions.manifest import ExtensionManifest
from personal_assistant.core.extensions.models import ExtensionRecord, ExtensionState


@dataclass(frozen=True, slots=True)
class RetainedVersion:
    extension_id: str
    version: str
    install_path: str
    artifact_hash: str
    state_schema_version: int
    manifest: ExtensionManifest


class VersionCatalog(Protocol):
    async def retained(self, extension_id: str) -> Sequence[RetainedVersion]: ...


class CompatibleVersionOperator:
    """Implements the existing ``VersionOperator`` port for real installations."""

    def __init__(self, catalog: VersionCatalog) -> None:
        self._catalog = catalog

    async def activate(self, old: ExtensionRecord, candidate: ExtensionRecord) -> None:
        candidate_path = Path(candidate.install_path or "")
        if not candidate_path.is_dir():
            raise ExtensionOperationError(
                "INSTALL_PATH_MISSING", "upgrade candidate is not installed"
            )
        # Memory catalogs learn retained versions from activation; the PostgreSQL
        # catalog reads the extension_versions table written by the lifecycle store.
        recorder = getattr(self._catalog, "record", None)
        if callable(recorder):
            recorder(old)
            recorder(candidate)

    async def rollback(self, current: ExtensionRecord) -> ExtensionRecord:
        versions = [
            version
            for version in await self._catalog.retained(current.manifest.id)
            if version.version != current.manifest.version
        ]
        if not versions:
            raise ExtensionOperationError(
                "ROLLBACK_NO_CANDIDATE",
                f"no retained version to roll back to: {current.manifest.id}",
            )
        compatible = [
            version
            for version in versions
            if version.state_schema_version >= current.manifest.state_schema_version
        ]
        if not compatible:
            raise ExtensionOperationError(
                "ROLLBACK_INCOMPATIBLE",
                "retained versions cannot read the current extension data schema",
            )
        chosen = max(compatible, key=lambda version: _semver_key(version.version))
        return ExtensionRecord(
            manifest=chosen.manifest,
            artifact_hash=chosen.artifact_hash,
            state=ExtensionState.DISABLED,
            install_path=chosen.install_path,
            data_retained=True,
        )


def _semver_key(version: str) -> tuple[int, int, int]:
    parts = version.split("-", 1)[0].split(".")
    numbers: list[int] = []
    for part in parts[:3]:
        try:
            numbers.append(int(part))
        except ValueError:
            numbers.append(0)
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2])


__all__ = ["CompatibleVersionOperator", "RetainedVersion", "VersionCatalog"]
