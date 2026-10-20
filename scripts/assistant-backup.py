"""Explicit backup/isolated restore; original data targets are protected."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from personal_assistant.bootstrap import build_container, build_secret_store
from personal_assistant.core.secrets import SecretHandle
from personal_assistant.infrastructure.database import PostgresAdapterConfig, PostgresDatabase
from personal_assistant.infrastructure.operations.backups import BackupCoordinator
from personal_assistant.infrastructure.operations.host import settings_from_profile
from personal_assistant.infrastructure.operations.recovery import (
    RecoveryVault,
    export_database,
    preflight_restore_target,
    restore_database,
    stage_artifacts,
)


async def run() -> dict[str, object]:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "list", "verify", "restore"])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--backup-id")
    parser.add_argument("--target-database-handle")
    parser.add_argument("--target-artifacts", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = settings_from_profile(args.profile, root=root)
    handle = os.getenv("PA_BACKUP_SECRET_HANDLE")
    if not handle or settings.storage_backend != "postgres":
        raise ValueError("backup requires persistent storage and a dedicated key handle")
    store = build_secret_store()
    container = build_container(settings)
    try:
        database = getattr(container.storage, "database", None)
        if not isinstance(database, PostgresDatabase):
            raise ValueError("backup requires PostgreSQL")

        async def export() -> dict[str, object]:
            await database.connect()
            return await export_database(database, settings.artifact_root)

        coordinator = BackupCoordinator(
            settings.extension_root.parent / "backups",
            RecoveryVault(store, SecretHandle(handle, "backup_key")),
            export,
            offsite=Path(os.environ["PA_BACKUP_OFFSITE_DIRECTORY"])
            if os.getenv("PA_BACKUP_OFFSITE_DIRECTORY")
            else None,
        )
        if args.action == "create":
            return {"ok": True, "backup": await coordinator.tick(force=True)}
        if args.action == "list":
            return {"ok": True, "items": coordinator.index()}
        snapshot = await coordinator.read(args.backup_id or "")
        if args.action == "verify":
            return {
                "ok": True,
                "tables": len(snapshot["tables"]),
                "artifacts": len(snapshot["artifacts"]),
                "credential_rebind_required": True,
            }
        if not args.target_database_handle or not args.target_artifacts:
            raise ValueError("restore requires new database and artifact targets")
        target_dsn = await store.resolve_for_broker(
            SecretHandle(args.target_database_handle, "database_dsn"), purpose="database.restore"
        )
        if not urlsplit(target_dsn.replace("postgresql+asyncpg://", "postgresql://")).path.endswith(
            ("_restore", "_test")
        ):
            raise ValueError("restore target must be a dedicated *_restore or *_test database")
        if (
            args.target_database_handle == settings.database_secret_handle
            or args.target_artifacts.resolve() == settings.artifact_root.resolve()
        ):
            raise ValueError("restore refuses the active original targets")
        target = PostgresDatabase(PostgresAdapterConfig("postgresql://unbound@127.0.0.1/unbound"))
        target.use_secret_reference(
            SecretHandle(args.target_database_handle, "database_dsn"), store
        )
        try:
            source_dsn = (
                await store.resolve_for_broker(
                    SecretHandle(settings.database_secret_handle, "database_dsn"),
                    purpose="database.restore.target-check",
                )
                if settings.database_secret_handle
                else settings.database_url
            )
            await target.connect()
            await preflight_restore_target(source_dsn, target)
            await target.startup()  # Only the verified empty isolated target may migrate.
            stage_artifacts(snapshot["artifacts"], args.target_artifacts.resolve())
            await restore_database(snapshot, target)
        finally:
            await target.close()
        return {
            "ok": True,
            "restored": True,
            "execution_frozen": True,
            "credential_rebind_required": True,
        }
    finally:
        await container.aclose()
        await container.storage.close()


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(run()), ensure_ascii=False))
    except Exception:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "BACKUP_OR_RESTORE_FAILED",
                    "message": "保留原库；检查凭据、备份认证、空目标和迁移版本。",
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None
