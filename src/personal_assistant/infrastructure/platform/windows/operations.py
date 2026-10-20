"""User-session Windows operations. No shell, passwords, or Session-0 browser automation."""

from __future__ import annotations

import asyncio
import getpass
import os
import re
import subprocess
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from xml.etree import ElementTree as ET

from personal_assistant.core.platform import FileChange, ProcessHandle, ProcessSpec

from .jobs import OwnedJob

_SECRET_ENV = re.compile(r"(?:PASSWORD|TOKEN|PRIVATE_KEY|CAPABILITY|API_KEY)$", re.I)
_SHELLS = {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "bash", "sh"}
_SECRET_ARGS = {"--password", "--token", "--api-key", "--dsn", "-p", "/rp"}
Runner = Callable[[Sequence[str]], Awaitable[tuple[int, str]]]


def safe_spec(spec: ProcessSpec) -> None:
    if not spec.argv or any(not isinstance(a, str) or "\x00" in a for a in spec.argv):
        raise ValueError("process arguments are invalid")
    if Path(spec.argv[0]).name.lower() in _SHELLS or any(
        a.lower() in _SECRET_ARGS for a in spec.argv
    ):
        raise ValueError("shells and credential arguments are not accepted")
    for argument in spec.argv:
        try:
            if urlsplit(argument).password is not None:
                raise ValueError("credential-bearing process argument is not accepted")
        except ValueError:
            raise ValueError("unsafe process argument") from None
    for key, value in spec.environment.items():
        if _SECRET_ENV.search(key) and not key.endswith("_SECRET_HANDLE"):
            raise ValueError("credential environment variables are not accepted")
        try:
            protected = urlsplit(value).password is not None
        except ValueError:
            protected = False
        if protected:
            raise ValueError("credential-bearing environment variables are not accepted")
    if not spec.cwd.is_dir():
        raise ValueError("process working directory is unavailable")


class WindowsProcessSupervisor:
    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._jobs: dict[str, OwnedJob] = {}

    async def start(self, spec: ProcessSpec) -> ProcessHandle:
        safe_spec(spec)
        environment = {
            k: v
            for k, v in os.environ.items()
            if (
                k.upper()
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
            )
        }
        environment.update(spec.environment)
        options = (
            {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {}
        )
        process = await asyncio.create_subprocess_exec(
            *spec.argv,
            cwd=spec.cwd,
            env=environment,
            stdin=asyncio.subprocess.PIPE if spec.stdin_json_rpc else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=options.get("creationflags", 0),
        )
        handle = ProcessHandle("process_" + uuid4().hex, process.pid)
        if os.name == "nt":
            try:
                self._jobs[handle.process_id] = OwnedJob(process.pid)
            except BaseException:
                if process.returncode is None:
                    process.terminate()
                await process.wait()
                raise
        self._processes[handle.process_id] = process
        return handle

    async def is_alive(self, handle: ProcessHandle) -> bool:
        process = self._processes.get(handle.process_id)
        return (
            process is not None
            and process.pid == handle.operating_system_pid
            and process.returncode is None
        )

    async def terminate(self, handle: ProcessHandle, grace_seconds: float = 5) -> None:
        process = self._processes.get(handle.process_id)
        if process is None or process.pid != handle.operating_system_pid:
            raise ValueError("process is not owned by this supervisor")
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), max(0.01, grace_seconds))
            except TimeoutError:
                process.kill()
                await process.wait()
        job = self._jobs.pop(handle.process_id, None)
        if job is not None:
            job.close()
        self._processes.pop(handle.process_id, None)


async def run_task_command(argv: Sequence[str]) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    return process.returncode or 0, output.decode(errors="replace")[:64_000]


def task_xml(argv: Sequence[str], *, cwd: Path, user_id: str) -> str:
    if not argv or any("\x00" in a for a in argv) or not user_id:
        raise ValueError("invalid startup specification")
    ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", ns)

    def node(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
        element = ET.SubElement(parent, "{" + ns + "}" + tag)
        element.text = text
        return element

    root = ET.Element("{" + ns + "}Task", version="1.2")
    trigger = node(node(root, "Triggers"), "LogonTrigger")
    node(trigger, "Enabled", "true")
    node(trigger, "UserId", user_id)
    principal = node(node(root, "Principals"), "Principal")
    principal.set("id", "Author")
    node(principal, "UserId", user_id)
    node(principal, "LogonType", "InteractiveToken")
    node(principal, "RunLevel", "LeastPrivilege")
    settings = node(root, "Settings")
    for key, value in {
        "MultipleInstancesPolicy": "IgnoreNew",
        "StartWhenAvailable": "true",
        "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false",
        "ExecutionTimeLimit": "PT0S",
        "Hidden": "true",
        "Enabled": "true",
    }.items():
        node(settings, key, value)
    actions = node(root, "Actions")
    actions.set("Context", "Author")
    execute = node(actions, "Exec")
    node(execute, "Command", argv[0])
    node(execute, "Arguments", subprocess.list2cmdline(list(argv[1:])))
    node(execute, "WorkingDirectory", str(cwd.resolve()))
    return ET.tostring(root, encoding="unicode")


class WindowsServiceManager:
    """Own-user logon guardian, deliberately not a LocalSystem GUI service."""

    def __init__(
        self,
        *,
        runner: Runner = run_task_command,
        user_id: str | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.runner, self.user_id, self.cwd = (
            runner,
            user_id or getpass.getuser(),
            cwd or Path.cwd(),
        )

    def name(self, service_name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", service_name):
            raise ValueError("invalid managed task name")
        return "PersonalAssistant." + service_name

    async def install(self, service_name: str, argv: Sequence[str]) -> None:
        name = self.name(service_name)
        safe_spec(ProcessSpec(tuple(argv), self.cwd))
        code, _ = await self.runner(("schtasks.exe", "/Query", "/TN", name))
        if code == 0:
            raise ValueError("managed startup task already exists; it was not overwritten")
        with tempfile.TemporaryDirectory(prefix="pa_task_") as directory:
            path = Path(directory) / "task.xml"
            path.write_text(task_xml(argv, cwd=self.cwd, user_id=self.user_id), encoding="utf-16")
            code, _ = await self.runner(("schtasks.exe", "/Create", "/TN", name, "/XML", str(path)))
            if code:
                raise ValueError("startup task registration failed")

    async def start(self, service_name: str) -> None:
        code, _ = await self.runner(("schtasks.exe", "/Run", "/TN", self.name(service_name)))
        if code:
            raise ValueError("startup task could not be started")

    async def stop(self, service_name: str) -> None:
        code, _ = await self.runner(("schtasks.exe", "/End", "/TN", self.name(service_name)))
        if code:
            raise ValueError("startup task could not be stopped")

    async def status(self, service_name: str) -> str:
        code, _ = await self.runner(("schtasks.exe", "/Query", "/TN", self.name(service_name)))
        return "REGISTERED" if code == 0 else "NOT_REGISTERED"

    async def uninstall(self, service_name: str) -> None:
        code, _ = await self.runner(
            ("schtasks.exe", "/Delete", "/TN", self.name(service_name), "/F")
        )
        if code:
            raise ValueError("startup task could not be removed")


class WindowsFileWatcher:
    """Bounded polling watcher; source reconciliation is still the correctness authority."""

    def __init__(self, *, interval: float = 1.0, max_files: int = 100_000) -> None:
        if interval <= 0 or max_files < 1:
            raise ValueError("watch limits must be positive")
        self.interval, self.max_files = interval, max_files

    def scan(self, roots: Sequence[Path]) -> dict[Path, tuple[int, int]]:
        result: dict[Path, tuple[int, int]] = {}
        for root in roots:
            if root.is_symlink() or not root.is_dir():
                raise ValueError("watch roots must be explicit local directories")
            for base, directories, files in os.walk(root, followlinks=False):
                directories[:] = [d for d in directories if not (Path(base) / d).is_symlink()]
                for filename in files:
                    path = Path(base) / filename
                    if path.is_symlink():
                        continue
                    stat = path.stat()
                    result[path] = stat.st_mtime_ns, stat.st_size
                    if len(result) > self.max_files:
                        raise ValueError("file watch limit exceeded; use source reconciliation")
        return result

    async def watch(self, roots: Sequence[Path]) -> AsyncIterator[FileChange]:
        previous = await asyncio.to_thread(self.scan, roots)
        while True:
            await asyncio.sleep(self.interval)
            current = await asyncio.to_thread(self.scan, roots)
            for path in current.keys() - previous.keys():
                yield FileChange(path, "created")
            for path in previous.keys() - current.keys():
                yield FileChange(path, "deleted")
            for path in current.keys() & previous.keys():
                if current[path] != previous[path]:
                    yield FileChange(path, "modified")
            previous = current
