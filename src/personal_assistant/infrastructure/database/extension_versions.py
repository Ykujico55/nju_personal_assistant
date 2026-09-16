"""Retained installed versions read from the durable ``extension_versions`` table."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from personal_assistant.infrastructure.extensions.versions import RetainedVersion

from .connection import PostgresDatabase
from .lifecycle_store import manifest_from_dict


def _artifact_hash(source_sha256: str | None) -> str:
    digest = (source_sha256 or "").strip()
    if not digest:
        return "sha256:" + "0" * 64
    return digest if digest.startswith("sha256:") else f"sha256:{digest}"


class PostgresVersionCatalog:
    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    async def retained(self, extension_id: str) -> Sequence[RetainedVersion]:
        async with self._db.connection() as connection:
            rows = await connection.fetch(
                """
                SELECT version, source_sha256, manifest, install_path
                FROM extension_versions
                WHERE extension_id = $1
                ORDER BY installed_at NULLS FIRST, version
                """,
                extension_id,
            )
        versions: list[RetainedVersion] = []
        for row in rows:
            install_path = row["install_path"]
            if not install_path or not Path(install_path).is_dir():
                continue
            manifest = row["manifest"]
            if not isinstance(manifest, dict) or not manifest:
                continue
            parsed = manifest_from_dict(manifest)
            versions.append(
                RetainedVersion(
                    extension_id=extension_id,
                    version=row["version"],
                    install_path=install_path,
                    artifact_hash=_artifact_hash(row["source_sha256"]),
                    state_schema_version=parsed.state_schema_version,
                    manifest=parsed,
                )
            )
        return tuple(versions)


__all__ = ["PostgresVersionCatalog"]
