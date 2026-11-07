"""Host profiles, a bounded guardian and metadata-only status. No external configuration changes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from personal_assistant.bootstrap import build_container, build_secret_store
from personal_assistant.core.platform import ProcessHandle, ProcessSpec
from personal_assistant.core.secrets import SecretHandle
from personal_assistant.infrastructure.database import PostgresDatabase
from personal_assistant.infrastructure.database.config import normalize_dsn
from personal_assistant.infrastructure.models.setup import KEYS, load_config
from personal_assistant.infrastructure.operations.backups import BackupCoordinator
from personal_assistant.infrastructure.operations.control import HostControl
from personal_assistant.infrastructure.operations.diagnostics import (
    HostDiagnosticError,
    classify,
    read_child_diagnostic,
)
from personal_assistant.infrastructure.operations.logging import MetadataLogger
from personal_assistant.infrastructure.operations.readiness import ready
from personal_assistant.infrastructure.operations.recovery import RecoveryVault, export_database
from personal_assistant.infrastructure.platform.windows.operations import WindowsProcessSupervisor
from personal_assistant.settings import Settings

HOST_KEYS = KEYS | frozenset(
    {
        "PA_MODEL_DISCLOSURE_MODE",
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
    try:
        values = load_profile(path)
        model = root / "var/config/model.json"
        if model.is_file() and not any(k in KEYS for k in values):
            values.update(load_config(model))
        # This process only; no raw secrets enter the environment.
        for key in list(os.environ):
            if key.startswith("PA_"):
                os.environ.pop(key)
        os.environ.update(values)
        os.environ["PA_DATABASE_URL"] = "postgresql://unbound@127.0.0.1/assistant"
        settings = Settings.from_env()
        settings.validate()
        return settings
    except Exception:
        raise HostDiagnosticError("HOST_PROFILE_INVALID") from None


def preflight_ports(settings: Settings, *, admin: bool) -> None:
    listeners = [("public", settings.public_port), ("health", settings.health_port)]
    if admin:
        listeners.append(("admin", settings.admin_port))
    seen: set[int] = set()
    for component, port in listeners:
        if port in seen:
            raise HostDiagnosticError("HOST_PORT_IN_USE", component=component)
        seen.add(port)
        try:
            with socket.socket() as probe:
                if os.name == "nt":
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", port))
        except OSError:
            raise HostDiagnosticError("HOST_PORT_IN_USE", component=component) from None


async def preflight_credentials(settings: Settings) -> None:
    checks = []
    if settings.storage_backend == "postgres" and settings.database_secret_handle:
        checks.append(
            (
                settings.database_secret_handle,
                "database_dsn",
                "database.connect",
                "HOST_DATABASE_CREDENTIAL_UNAVAILABLE",
            )
        )
    if settings.model_remote_secret_handle:
        checks.append(
            (
                settings.model_remote_secret_handle,
                "model_api_key",
                f"model:{settings.model_remote_provider_id}:complete",
                "HOST_MODEL_CREDENTIAL_UNAVAILABLE",
            )
        )
    for handle, kind, purpose, code in checks:
        value: object = None
        try:
            value = await build_secret_store().resolve_for_broker(
                SecretHandle(handle, kind),
                purpose=purpose,
            )
            if not isinstance(value, str):
                raise ValueError()
            if kind == "database_dsn":
                if not normalize_dsn(value).startswith("postgresql://"):
                    raise ValueError()
            elif re.fullmatch(r"[\x21-\x7e]{1,4096}", value) is None:
                raise ValueError()
        except Exception:
            value = None
            raise HostDiagnosticError(code) from None
        finally:
            value = None


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
            raise HostDiagnosticError("HOST_ALREADY_RUNNING") from None

    def close(self) -> None:
        self.file.close()


async def guard(
    profile: Path,
    root: Path,
    *,
    admin: bool = False,
    min_free_bytes: int = 256 * 1024 * 1024,
    boot_id: str | None = None,
) -> None:
    settings = settings_from_profile(profile, root=root)
    control = HostControl(settings.extension_root.parent / "control")
    lock = GuardianLock(control.root / "guardian.lock")
    if boot_id is None:
        try:
            coordination = GuardianLock(control.root / "restart.lock")
        except HostDiagnosticError:
            lock.close()
            raise HostDiagnosticError("HOST_RESTART_IN_PROGRESS") from None
        coordination.close()
    boot_id = boot_id or uuid.uuid4().hex
    if re.fullmatch(r"[0-9a-f]{32}", boot_id) is None:
        lock.close()
        raise HostDiagnosticError("HOST_PROFILE_INVALID")
    supervisor = WindowsProcessSupervisor()
    control.root.mkdir(parents=True, exist_ok=True)
    components = (
        ["public", "health"]
        + (["agent"] if settings.storage_backend == "postgres" else [])
        + (["admin"] if admin else [])
    )
    handles, restarts = {}, dict.fromkeys(components, 0)
    logger = MetadataLogger(control.root.parent / "logs")
    backup_container = None
    backup = None
    failure: HostDiagnosticError | None = None
    last_diagnostic: HostDiagnosticError | None = None

    async def start(name: str) -> ProcessHandle:
        (control.root / ("diagnostic-" + name + ".json")).unlink(missing_ok=True)
        (control.root / ("ready-" + name + ".json")).unlink(missing_ok=True)
        try:
            return await supervisor.start(
                ProcessSpec(
                    argv=(
                        sys.executable,
                        "-m",
                        "personal_assistant.host_entry",
                        "child-" + name,
                        "--profile",
                        str(profile.resolve()),
                        "--boot-id",
                        boot_id,
                    ),
                    cwd=root,
                    stdin_json_rpc=False,
                )
            )
        except Exception:
            raise HostDiagnosticError("HOST_COMPONENT_START_FAILED", component=name) from None

    try:
        preflight_ports(settings, admin=admin)
        await preflight_credentials(settings)
        control.prepare_start(boot_id)
        control._write(
            "status.json",
            {
                "seen_at": datetime.now(UTC).isoformat(),
                "guardian_pid": os.getpid(),
                "parent_pid": os.getppid(),
                "boot_id": boot_id,
                "admin": admin,
                "components": {},
                "ready": False,
                "frozen": control.frozen(),
            },
        )
        backup_handle = os.getenv("PA_BACKUP_SECRET_HANDLE")
        if backup_handle and settings.storage_backend == "postgres":
            backup_container = build_container(settings)
            database = getattr(backup_container.storage, "database", None)
            if not isinstance(database, PostgresDatabase):
                raise HostDiagnosticError("HOST_DATABASE_UNAVAILABLE")

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
        while not control.stopping(boot_id):
            free = shutil.disk_usage(root).free
            logger.prune()
            if backup is not None:
                try:
                    receipt = await backup.tick()
                    if receipt:
                        logger.emit("backup.completed", component="backup")
                except Exception:
                    control.freeze(owner="fault")
                    logger.emit("backup.failed", code="BACKUP_FAILED")
            if free < min_free_bytes:
                control.freeze(owner="fault")
            for name, handle in list(handles.items()):
                if not await supervisor.is_alive(handle):
                    last_diagnostic = read_child_diagnostic(
                        control.root,
                        name,
                        handle.operating_system_pid,
                    ) or HostDiagnosticError("HOST_COMPONENT_EXITED", component=name)
                    await supervisor.terminate(handle)
                    if restarts[name] >= 3:
                        control.freeze(owner="fault")
                        raise last_diagnostic
                    restarts[name] += 1
                    await asyncio.sleep(min(2 ** restarts[name], 8))
                    handles[name] = await start(name)
                    logger.emit("component.restarted", component=name, restart_count=restarts[name])
            control._write(
                "status.json",
                {
                    "seen_at": datetime.now(UTC).isoformat(),
                    "guardian_pid": os.getpid(),
                    "parent_pid": os.getppid(),
                    "boot_id": boot_id,
                    "admin": admin,
                    "ready": all(
                        ready(control, n, h.operating_system_pid, boot_id)
                        for n, h in handles.items()
                    ),
                    "components": {n: h.operating_system_pid for n, h in handles.items()},
                    "frozen": control.frozen(),
                    "disk_warning": free < min_free_bytes,
                    "diagnostic": last_diagnostic.metadata() if last_diagnostic else None,
                },
            )
            await asyncio.sleep(1)
    except Exception as exc:
        failure = classify(exc)
        raise failure from None
    finally:
        try:
            if handles:
                control.freeze(owner="guardian", preserve_existing=True)
                control.request_stop(boot_id=boot_id)
                stop_deadline = time.monotonic() + 5
                while time.monotonic() < stop_deadline:
                    if not any([await supervisor.is_alive(h) for h in handles.values()]):
                        break
                    await asyncio.sleep(0.05)
            for handle in handles.values():
                with contextlib.suppress(Exception):
                    await supervisor.terminate(handle, grace_seconds=5)
            control._write(
                "status.json",
                {
                    "seen_at": datetime.now(UTC).isoformat(),
                    "stopped": True,
                    "ready": False,
                    "guardian_pid": os.getpid(),
                    "parent_pid": os.getppid(),
                    "boot_id": boot_id,
                    "admin": admin,
                    "frozen": control.frozen(),
                    "diagnostic": failure.metadata()
                    if failure
                    else (last_diagnostic.metadata() if last_diagnostic else None),
                },
            )
            if backup_container is not None:
                try:
                    await backup_container.aclose()
                finally:
                    await backup_container.storage.close()
        finally:
            lock.close()


def guardian_running(control: HostControl) -> bool:
    try:
        probe = GuardianLock(control.root / "guardian.lock")
    except HostDiagnosticError:
        return True
    probe.close()
    return False


def spawn_guardian(
    profile: Path, root: Path, *, admin: bool, boot_id: str
) -> subprocess.Popen[bytes]:
    """Detach the new guardian; its children remain owned by its own supervisor."""
    argv = [
        sys.executable,
        "-Xutf8",
        "-m",
        "personal_assistant.host_entry",
        "serve",
        "--profile",
        str(profile.resolve()),
        "--boot-id",
        boot_id,
    ]
    if admin:
        argv.append("--admin")
    environment = {
        k: v
        for k, v in os.environ.items()
        if k.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "PATH",
            "PATHEXT",
            "TEMP",
            "TMP",
            "APPDATA",
            "LOCALAPPDATA",
            "USERPROFILE",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "PROGRAMDATA",
            "PYTHONUTF8",
            "PYTHONPATH",
        }
    }
    return subprocess.Popen(
        argv,
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        if os.name == "nt"
        else 0,
        start_new_session=os.name != "nt",
    )


async def restart(
    profile: Path, root: Path, *, admin: bool | None = None, timeout_seconds: float = 60
) -> dict[str, Any]:
    settings = settings_from_profile(profile, root=root)
    control = HostControl(settings.extension_root.parent / "control")
    try:
        coordination = GuardianLock(control.root / "restart.lock")
    except HostDiagnosticError:
        raise HostDiagnosticError("HOST_RESTART_IN_PROGRESS") from None
    ticket: str | None = None
    owner = "restart-" + uuid.uuid4().hex
    boot_id = uuid.uuid4().hex
    spawned: subprocess.Popen[bytes] | None = None
    began = False
    try:
        if not guardian_running(control):
            raise HostDiagnosticError("HOST_NOT_RUNNING")
        original = control.read("status.json")
        old_boot = original.get("boot_id")
        if (
            not isinstance(old_boot, str)
            or re.fullmatch(r"[0-9a-f]{32}", old_boot) is None
            or type(original.get("admin")) is not bool
            or type(original.get("guardian_pid")) is not int
        ):
            raise HostDiagnosticError("HOST_RESTART_UNSUPPORTED")
        selected_admin = original["admin"] if admin is None else admin
        ticket = control.begin_restart(owner)
        began = True
        control.request_stop(boot_id=old_boot)
        deadline = time.monotonic() + max(0.01, timeout_seconds)
        while guardian_running(control):
            if time.monotonic() >= deadline:
                raise HostDiagnosticError("HOST_STOP_TIMEOUT")
            await asyncio.sleep(0.05)
        spawned = spawn_guardian(profile, root, admin=selected_admin, boot_id=boot_id)
        while time.monotonic() < deadline:
            status = control.read("status.json")
            matched = (
                status.get("boot_id") == boot_id
                and type(status.get("guardian_pid")) is int
                and (
                    status["guardian_pid"] == spawned.pid
                    or (
                        type(status.get("parent_pid")) is int
                        and status["parent_pid"] == spawned.pid
                    )
                )
            )
            if matched and status.get("ready") is True and spawned.poll() is None:
                resumed = control.resume_restart(ticket, owner)
                return {
                    "ok": True,
                    "ready": True,
                    "guardian_pid": status["guardian_pid"],
                    "launcher_pid": spawned.pid,
                    "boot_id": boot_id,
                    "admin": selected_admin,
                    "resumed": resumed,
                    "frozen": control.frozen(),
                }
            if spawned.poll() is not None:
                diagnostic = status.get("diagnostic") if matched else None
                if isinstance(diagnostic, dict):
                    raise HostDiagnosticError(
                        diagnostic.get("code", "HOST_COMPONENT_EXITED"),
                        diagnostic_id=diagnostic.get("diagnostic_id"),
                    )
                raise HostDiagnosticError("HOST_COMPONENT_START_FAILED")
            await asyncio.sleep(0.1)
        raise HostDiagnosticError("HOST_READY_TIMEOUT")
    except BaseException:
        if began:
            control.freeze(owner="fault", preserve_existing=True)
            if spawned is not None:
                control.request_stop(boot_id=boot_id)
        raise
    finally:
        coordination.close()
