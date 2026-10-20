"""Host profiles, a bounded guardian and metadata-only status. No external configuration changes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from personal_assistant.bootstrap import build_container, build_secret_store
from personal_assistant.core.platform import ProcessHandle, ProcessSpec
from personal_assistant.core.secrets import SecretHandle
from personal_assistant.infrastructure.database import PostgresDatabase
from personal_assistant.infrastructure.models.setup import KEYS, load_config
from personal_assistant.infrastructure.operations.backups import BackupCoordinator
from personal_assistant.infrastructure.operations.control import HostControl
from personal_assistant.infrastructure.operations.logging import MetadataLogger
from personal_assistant.infrastructure.operations.recovery import RecoveryVault, export_database
from personal_assistant.infrastructure.platform.windows.operations import WindowsProcessSupervisor
from personal_assistant.settings import Settings

HOST_KEYS = KEYS | frozenset(
    {
        "PA_ENVIRONMENT",
        "PA_LOG_LEVEL",
        "PA_PUBLIC_HOST",
        "PA_PUBLIC_PORT",
        "PA_ADMIN_HOST",
        "PA_ADMIN_PORT",
        "PA_HEALTH_HOST",
        "PA_HEALTH_PORT",
        "PA_STORAGE_BACKEND",
        "PA_DATABASE_SECRET_HANDLE",
        "PA_OWNER_ID",
        "PA_EXTENSION_ROOT",
        "PA_ARTIFACT_ROOT",
        "PA_TRUST_CLOUDFLARE_ACCESS",
        "PA_PUBLIC_ORIGIN",
        "PA_CF_ACCESS_TEAM_DOMAIN",
        "PA_CF_ACCESS_AUD",
        "PA_MAIL_SEND_ENABLED",
        "PA_MAIL_TEST_RECIPIENTS",
        "PA_BROWSER_COMPANION_URL",
        "PA_BROWSER_ALLOWED_ORIGINS",
        "PA_BROWSER_SUBMIT_ENABLED",
        "PA_PUSH_VAPID_PUBLIC_KEY",
        "PA_PUSH_VAPID_SECRET_HANDLE",
        "PA_PUSH_ALLOWED_ORIGINS",
        "PA_NTFY_PUBLISH_URL",
        "PA_NTFY_SECRET_HANDLE",
        "PA_EMBEDDED_AGENT",
        "PA_BACKUP_SECRET_HANDLE",
        "PA_BACKUP_OFFSITE_DIRECTORY",
    }
)


def load_profile(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or set(value) - HOST_KEYS
        or any(not isinstance(v, str) or "\x00" in v or "\n" in v for v in value.values())
    ):
        raise ValueError("profile contains unsupported or secret settings")
    for v in value.values():
        try:
            if urlsplit(v).password:
                raise ValueError()
        except ValueError:
            raise ValueError("credential-bearing profiles are refused") from None
    if value.get("PA_PUBLIC_HOST", "127.0.0.1") not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("the host profile must keep all listeners on loopback")
    if value.get("PA_STORAGE_BACKEND") == "postgres" and not value.get("PA_DATABASE_SECRET_HANDLE"):
        raise ValueError("persistent host profiles require a database SecretHandle")
    return value


def settings_from_profile(path: Path, *, root: Path) -> Settings:
    values = load_profile(path)
    model = root / "var/config/model.json"
    if model.is_file() and not any(k.startswith("PA_MODEL_") for k in values):
        values.update(load_config(model))
    # The child process only; no raw secrets enter the environment.
    for key in list(os.environ):
        if key.startswith("PA_"):
            os.environ.pop(key)
    os.environ.update(values)
    os.environ["PA_DATABASE_URL"] = "postgresql://unbound@127.0.0.1/assistant"
    return Settings.from_env()


class GuardianLock:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import importlib

                fcntl = importlib.import_module("fcntl")

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError("another guardian owns this profile") from None

    def close(self) -> None:
        self.file.close()


async def guard(
    profile: Path, root: Path, *, admin: bool = False, min_free_bytes: int = 256 * 1024 * 1024
) -> None:
    settings = settings_from_profile(profile, root=root)
    control = HostControl(settings.extension_root.parent / "control")
    lock = GuardianLock(control.root / "guardian.lock")
    supervisor = WindowsProcessSupervisor()
    control.root.mkdir(parents=True, exist_ok=True)
    (control.root / "stop.json").unlink(missing_ok=True)
    components = (
        ["public", "health"]
        + (["agent"] if settings.storage_backend == "postgres" else [])
        + (["admin"] if admin else [])
    )
    handles, restarts = {}, dict.fromkeys(components, 0)
    logger = MetadataLogger(control.root.parent / "logs")
    backup_container = None
    backup = None

    async def start(name: str) -> ProcessHandle:
        return await supervisor.start(
            ProcessSpec(
                argv=(
                    sys.executable,
                    "-m",
                    "personal_assistant.host_entry",
                    "child-" + name,
                    "--profile",
                    str(profile.resolve()),
                ),
                cwd=root,
                stdin_json_rpc=False,
            )
        )

    try:
        backup_handle = os.getenv("PA_BACKUP_SECRET_HANDLE")
        if backup_handle and settings.storage_backend == "postgres":
            backup_container = build_container(settings)
            database = getattr(backup_container.storage, "database", None)
            if not isinstance(database, PostgresDatabase):
                lock.close()
                raise ValueError("backup database is unavailable")

            async def export() -> dict[str, Any]:
                await database.connect()
                return await export_database(database, settings.artifact_root)

            backup = BackupCoordinator(
                control.root.parent / "backups",
                RecoveryVault(build_secret_store(), SecretHandle(backup_handle, "backup_key")),
                export,
                offsite=Path(os.environ["PA_BACKUP_OFFSITE_DIRECTORY"])
                if os.getenv("PA_BACKUP_OFFSITE_DIRECTORY")
                else None,
            )
        for name in components:
            handles[name] = await start(name)
            logger.emit("component.started", component=name, pid=handles[name].operating_system_pid)
        while not control.stopping():
            free = shutil.disk_usage(root).free
            logger.prune()
            if backup is not None:
                try:
                    receipt = await backup.tick()
                    if receipt:
                        logger.emit("backup.completed", component="backup")
                except Exception:
                    control.freeze()
                    logger.emit("backup.failed", code="BACKUP_FAILED")
            if free < min_free_bytes:
                control.freeze()
            for name, handle in list(handles.items()):
                if not await supervisor.is_alive(handle):
                    await supervisor.terminate(handle)
                    if restarts[name] >= 3:
                        control.freeze()
                        raise ValueError("component restart limit reached; manual review required")
                    restarts[name] += 1
                    await asyncio.sleep(min(2 ** restarts[name], 8))
                    handles[name] = await start(name)
                    logger.emit("component.restarted", component=name, restart_count=restarts[name])
            control._write(
                "status.json",
                {
                    "seen_at": datetime.now(UTC).isoformat(),
                    "guardian_pid": os.getpid(),
                    "components": {n: h.operating_system_pid for n, h in handles.items()},
                    "frozen": control.frozen(),
                    "disk_warning": free < min_free_bytes,
                },
            )
            await asyncio.sleep(1)
    finally:
        control.freeze()
        for handle in handles.values():
            with contextlib.suppress(Exception):
                await supervisor.terminate(handle, grace_seconds=5)
        control._write(
            "status.json",
            {"seen_at": datetime.now(UTC).isoformat(), "stopped": True, "frozen": True},
        )
        try:
            if backup_container is not None:
                try:
                    await backup_container.aclose()
                finally:
                    await backup_container.storage.close()
        finally:
            lock.close()
