"""PostgreSQL persistence for extension lifecycle state (F01 scope only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from personal_assistant.core.extensions.errors import ExtensionError
from personal_assistant.core.extensions.manifest import (
    DeclaredCapabilities,
    ExtensionManifest,
    ManifestTool,
)
from personal_assistant.core.extensions.models import ExtensionRecord, ExtensionState

from .connection import PostgresDatabase

_MANIFEST_SLOT_KEYS = (
    "event_sources",
    "context_providers",
    "workflows",
    "schedules",
    "notifications",
    "migrations",
    "forms",
)


def _bare_sha256(value: str) -> str:
    digest = value.split(":", 1)[1] if ":" in value else value
    return digest[:64]


def manifest_to_dict(manifest: ExtensionManifest) -> dict[str, Any]:
    """Serialize an extension manifest for durable storage."""
    data: dict[str, Any] = {
        "root": str(manifest.root),
        "manifest_version": manifest.manifest_version,
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "core_api": manifest.core_api,
        "python": manifest.python,
        "entrypoint": manifest.entrypoint,
        "dependency_lock": manifest.dependency_lock,
        "config_schema": manifest.config_schema,
        "state_schema_version": manifest.state_schema_version,
        "healthcheck": manifest.healthcheck,
        "tools": [
            {
                "id": tool.id,
                "risk": tool.risk,
                "input_schema": tool.input_schema,
                "output_schema": tool.output_schema,
            }
            for tool in manifest.tools
        ],
        "capabilities": {
            "required": list(manifest.capabilities.required),
            "optional": list(manifest.capabilities.optional),
        },
    }
    for key in _MANIFEST_SLOT_KEYS:
        data[key] = list(getattr(manifest, key))
    return data


def manifest_from_dict(data: dict[str, Any]) -> ExtensionManifest:
    capabilities = data.get("capabilities") or {}
    tools = tuple(
        ManifestTool(
            id=item["id"],
            risk=item["risk"],
            input_schema=item["input_schema"],
            output_schema=item["output_schema"],
        )
        for item in data.get("tools", [])
    )
    slot_values = {key: tuple(data.get(key) or ()) for key in _MANIFEST_SLOT_KEYS}
    return ExtensionManifest(
        root=Path(data["root"]),
        manifest_version=data["manifest_version"],
        id=data["id"],
        name=data["name"],
        version=data["version"],
        core_api=data["core_api"],
        python=data["python"],
        entrypoint=data["entrypoint"],
        dependency_lock=data["dependency_lock"],
        config_schema=data.get("config_schema"),
        state_schema_version=data["state_schema_version"],
        healthcheck=data["healthcheck"],
        tools=tools,
        capabilities=DeclaredCapabilities(
            required=tuple(capabilities.get("required") or ()),
            optional=tuple(capabilities.get("optional") or ()),
        ),
        **slot_values,
    )


_SELECT_COLUMNS = (
    "id, lifecycle_state, retained_data, tombstone, install_path, artifact_hash, manifest"
)


class PostgresLifecycleStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    async def get(self, extension_id: str) -> ExtensionRecord | None:
        async with self._db.connection() as connection:
            row = await connection.fetchrow(
                f"SELECT {_SELECT_COLUMNS} FROM extensions WHERE id = $1",
                extension_id,
            )
        return self._record_from_row(row) if row is not None else None

    async def all(self) -> tuple[ExtensionRecord, ...]:
        async with self._db.connection() as connection:
            rows = await connection.fetch(
                f"SELECT {_SELECT_COLUMNS} FROM extensions ORDER BY id"
            )
        return tuple(self._record_from_row(row) for row in rows)

    @staticmethod
    def _record_from_row(row: Any) -> ExtensionRecord:
        manifest = row["manifest"]
        if not isinstance(manifest, dict) or not manifest:
            raise ExtensionError(
                f"extension {row['id']} has no persisted manifest"
            )
        return ExtensionRecord(
            manifest=manifest_from_dict(manifest),
            artifact_hash=row["artifact_hash"] or "",
            state=ExtensionState(row["lifecycle_state"]),
            install_path=row["install_path"],
            data_retained=row["retained_data"],
            tombstone=row["tombstone"],
        )

    async def save(self, record: ExtensionRecord) -> None:
        manifest = manifest_to_dict(record.manifest)
        async with self._db.transaction(), self._db.connection() as connection:
            # Serialize concurrent lifecycle operations for the same extension.
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"extension:{record.manifest.id}",
            )
            await connection.execute(
                """
                INSERT INTO extensions (
                    id, active_version, lifecycle_state, retained_data,
                    registry_generation, updated_at, manifest, manifest_version,
                    artifact_hash, install_path, tombstone
                ) VALUES ($1, $2, $3, $4, 1, now(), $5, $6, $7, $8, $9)
                ON CONFLICT (id) DO UPDATE SET
                    active_version = EXCLUDED.active_version,
                    lifecycle_state = EXCLUDED.lifecycle_state,
                    retained_data = EXCLUDED.retained_data,
                    registry_generation = extensions.registry_generation + 1,
                    updated_at = now(),
                    manifest = EXCLUDED.manifest,
                    manifest_version = EXCLUDED.manifest_version,
                    artifact_hash = EXCLUDED.artifact_hash,
                    install_path = EXCLUDED.install_path,
                    tombstone = EXCLUDED.tombstone
                """,
                record.manifest.id,
                record.manifest.version,
                record.state.value,
                record.data_retained,
                manifest,
                record.manifest.manifest_version,
                record.artifact_hash,
                record.install_path,
                record.tombstone,
            )
            await connection.execute(
                """
                INSERT INTO extension_versions (
                    extension_id, version, source_sha256, manifest, install_path, installed_at
                ) VALUES ($1, $2, $3, $4, $5, now())
                ON CONFLICT (extension_id, version) DO UPDATE SET
                    source_sha256 = EXCLUDED.source_sha256,
                    manifest = EXCLUDED.manifest,
                    install_path = EXCLUDED.install_path
                """,
                record.manifest.id,
                record.manifest.version,
                _bare_sha256(record.artifact_hash),
                manifest,
                record.install_path,
            )


# Backwards-compatible aliases for the private names used by the F01 test suite.
_manifest_to_dict = manifest_to_dict
_manifest_from_dict = manifest_from_dict
