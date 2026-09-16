"""In-memory retained-version catalog (development and tests only)."""

from __future__ import annotations

from collections.abc import Sequence

from personal_assistant.core.extensions.models import ExtensionRecord
from personal_assistant.infrastructure.extensions.versions import RetainedVersion


class InMemoryVersionCatalog:
    def __init__(self) -> None:
        self._versions: dict[str, dict[str, RetainedVersion]] = {}

    def record(self, record: ExtensionRecord) -> None:
        if record.install_path is None:
            return
        retained = RetainedVersion(
            extension_id=record.manifest.id,
            version=record.manifest.version,
            install_path=record.install_path,
            artifact_hash=record.artifact_hash,
            state_schema_version=record.manifest.state_schema_version,
            manifest=record.manifest,
        )
        self._versions.setdefault(record.manifest.id, {})[record.manifest.version] = retained

    async def retained(self, extension_id: str) -> Sequence[RetainedVersion]:
        return tuple(self._versions.get(extension_id, {}).values())


__all__ = ["InMemoryVersionCatalog"]
