"""Atomic, business-agnostic extension capability registry."""

from __future__ import annotations

from collections.abc import Iterable
from threading import RLock

from .errors import DuplicateCapabilityError, ExtensionError
from .manifest import ExtensionManifest, ManifestParser, discover_manifests
from .models import CapabilityOwner, ExtensionRecord, ExtensionState, RegistrySnapshot


class ExtensionRegistry:
    """Publishes immutable snapshots so active runs never see half an update."""

    def __init__(self, parser: ManifestParser | None = None) -> None:
        self._parser = parser or ManifestParser()
        self._lock = RLock()
        self._snapshot = RegistrySnapshot(generation=0)

    @property
    def snapshot(self) -> RegistrySnapshot:
        with self._lock:
            return self._snapshot

    def discover(self, parent: str) -> tuple[ExtensionManifest, ...]:
        """Statically find manifests; this method never imports an extension."""

        manifests = discover_manifests(parent, self._parser)
        duplicates = _duplicates(manifest.id for manifest in manifests)
        if duplicates:
            raise ExtensionError(f"duplicate extension ids: {duplicates}")
        return manifests

    def enable(self, record: ExtensionRecord) -> RegistrySnapshot:
        if record.state is not ExtensionState.ENABLED:
            raise ExtensionError("only a healthy ENABLED record can enter the registry")
        with self._lock:
            extensions = dict(self._snapshot.extensions)
            extensions[record.manifest.id] = record
            self._snapshot = self._build(self._snapshot.generation + 1, extensions.values())
            return self._snapshot

    def disable(self, extension_id: str) -> RegistrySnapshot:
        """Atomically revoke every capability owned by an extension."""

        with self._lock:
            extensions = dict(self._snapshot.extensions)
            extensions.pop(extension_id, None)
            self._snapshot = self._build(self._snapshot.generation + 1, extensions.values())
            return self._snapshot

    def replace_all(self, records: Iterable[ExtensionRecord]) -> RegistrySnapshot:
        records = tuple(records)
        invalid = [
            record.manifest.id
            for record in records
            if record.state is not ExtensionState.ENABLED
        ]
        if invalid:
            raise ExtensionError(f"registry records are not enabled: {invalid}")
        with self._lock:
            self._snapshot = self._build(self._snapshot.generation + 1, records)
            return self._snapshot

    @staticmethod
    def _build(generation: int, records: Iterable[ExtensionRecord]) -> RegistrySnapshot:
        extensions: dict[str, ExtensionRecord] = {}
        capabilities: dict[str, CapabilityOwner] = {}
        for record in records:
            manifest = record.manifest
            if manifest.id in extensions:
                raise DuplicateCapabilityError(f"extension id already registered: {manifest.id}")
            extensions[manifest.id] = record
            for slot, identifiers in manifest.slots.items():
                for identifier in identifiers:
                    previous = capabilities.get(identifier)
                    if previous is not None:
                        raise DuplicateCapabilityError(
                            f"capability {identifier!r} is declared by both "
                            f"{previous.extension_id!r} and {manifest.id!r}"
                        )
                    capabilities[identifier] = CapabilityOwner(
                        extension_id=manifest.id,
                        extension_version=manifest.version,
                        slot=slot,
                    )
        return RegistrySnapshot(
            generation=generation,
            extensions=extensions,
            capabilities=capabilities,
        )


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)
