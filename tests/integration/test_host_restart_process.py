"""CORE.HOST.1: real Windows children, isolated memory profile, no original instance."""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import httpx

from personal_assistant.infrastructure.operations.control import HostControl
from personal_assistant.infrastructure.platform.windows.jobs import OwnedJob

REPOSITORY = Path(__file__).resolve().parents[2]


def _running(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    kernel.GetExitCodeProcess.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x1000, 0, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_uint32()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError("cannot inspect owned test process")
        return code.value == 259
    finally:
        kernel.CloseHandle(handle)


def _belongs_to(job: OwnedJob, pid: int) -> bool:
    """Check native ownership, including a venv launcher redirector's child."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.IsProcessInJob.argtypes = (
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
    )
    kernel.IsProcessInJob.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x1000, 0, pid)
    if not handle:
        return False
    try:
        owned = ctypes.c_int()
        if not kernel.IsProcessInJob(handle, job.handle, ctypes.byref(owned)):
            raise OSError("cannot inspect owned test job")
        return bool(owned.value)
    finally:
        kernel.CloseHandle(handle)


@unittest.skipUnless(sys.platform == "win32", "native Windows guardian/process ownership")
class HostRestartProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pa_host_restart_")
        self.root = Path(self.temporary.name).resolve()
        self.control = HostControl(self.root / "var" / "control")
        self.processes: list[subprocess.Popen[bytes]] = []
        self.jobs: list[OwnedJob] = []
        self.pids: set[int] = set()
        sockets = [socket.socket() for _ in range(3)]
        try:
            for endpoint in sockets:
                endpoint.bind(("127.0.0.1", 0))
            self.ports = [endpoint.getsockname()[1] for endpoint in sockets]
        finally:
            for endpoint in sockets:
                endpoint.close()
        self.profile = self.root / "host.json"
        self.profile.write_text(
            json.dumps(
                {
                    "PA_ENVIRONMENT": "development",
                    "PA_STORAGE_BACKEND": "memory",
                    "PA_EXTENSION_ROOT": str(self.root / "var" / "extensions"),
                    "PA_ARTIFACT_ROOT": str(self.root / "var" / "artifacts"),
                    "PA_PUBLIC_HOST": "127.0.0.1",
                    "PA_PUBLIC_PORT": str(self.ports[0]),
                    "PA_ADMIN_PORT": str(self.ports[1]),
                    "PA_HEALTH_PORT": str(self.ports[2]),
                    "PA_EMBEDDED_AGENT": "false",
                    "PA_MAIL_SEND_ENABLED": "false",
                    "PA_BROWSER_SUBMIT_ENABLED": "false",
                }
            ),
            encoding="utf-8",
        )
        self.environment = {
            name: value for name, value in os.environ.items() if not name.upper().startswith("PA_")
        }
        self.environment["PYTHONPATH"] = os.pathsep.join(
            str(REPOSITORY / source) for source in ("src", "extension_sdk/src")
        )
        self.environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self.client = httpx.Client(trust_env=False, timeout=0.5)

    def _spawn(self, action: str, *extra: str) -> tuple[subprocess.Popen[bytes], Path]:
        log = self.root / f"{len(self.processes)}-{action}.log"
        with log.open("wb") as output:
            process = subprocess.Popen(
                [
                    sys.executable, "-Xutf8", "-B", "-m", "personal_assistant.host_entry",
                    action, "--profile", str(self.profile), *extra,
                ],
                cwd=self.root,
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        self.processes.append(process)
        self.pids.add(process.pid)
        try:
            # The test owns both serve and restart process trees. Closing these jobs
            # also reaps a detached replacement guardian if any assertion fails.
            self.jobs.append(OwnedJob(process.pid))
        except BaseException:
            process.terminate()
            process.wait(timeout=5)
            raise
        return process, log

    def _command(self, action: str, *extra: str) -> tuple[int, str]:
        process, log = self._spawn(action, *extra)
        code = process.wait(timeout=75 if action == "restart" else 20)
        return code, log.read_text(encoding="utf-8", errors="replace")

    def _status(self) -> dict[str, Any]:
        path = self.control.root / "status.json"
        if not path.exists():
            return {}
        status: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        guardian = status.get("guardian_pid")
        if isinstance(guardian, int):
            self.pids.add(guardian)
        for child in status.get("components", {}).values():
            if isinstance(child, int):
                self.pids.add(child)
        return status

    def _healthy(self) -> bool:
        expected = [
            {"status": "ok", "storage": "memory"},
            {"status": "ok", "scope": "local-admin"},
            {"status": "ok"},
        ]
        try:
            return all(
                (response := self.client.get(f"http://127.0.0.1:{port}/healthz")).status_code
                == 200
                and response.json() == value
                for port, value in zip(self.ports, expected, strict=True)
            )
        except (httpx.HTTPError, ValueError):
            return False

    def _wait_started(self, guardian: subprocess.Popen[bytes]) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = self._status()
            if (
                type(status.get("guardian_pid")) is int
                and _belongs_to(self.jobs[0], status["guardian_pid"])
                and status.get("ready") is True
                and set(status.get("components", {})) == {"public", "admin", "health"}
                and self._healthy()
            ):
                return status
            self.assertIsNone(guardian.poll(), "isolated guardian exited during startup")
            time.sleep(0.1)
        self.fail("isolated memory host did not become healthy within startup budget")

    def _component_pids(self, status: dict[str, Any]) -> set[int]:
        actual_pids = set()
        for component in status["components"]:
            evidence = json.loads(
                (self.control.root / f"ready-{component}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["boot_id"], evidence["boot_id"])
            pid = evidence["pid"]
            self.assertIs(type(pid), int)
            self.assertTrue(any(_belongs_to(job, pid) for job in self.jobs))
            actual_pids.add(pid)
        self.pids.update(actual_pids)
        return actual_pids

    def _exercise_restart(self, initially_frozen: bool) -> None:
        if initially_frozen:
            self.control.freeze()
        guardian, _ = self._spawn("serve", "--admin")
        before = self._wait_started(guardian)
        original_actual_pids = self._component_pids(before)
        self.assertEqual(initially_frozen, self.control.frozen())
        if not initially_frozen:
            # A normal Windows reader prevents replace while its handle is open.
            # Span one heartbeat and prove this bounded conflict cannot kill the host.
            with (self.control.root / "status.json").open("rb"):
                time.sleep(1.3)
            deadline = time.monotonic() + 5
            current = self._status()
            while current.get("seen_at") == before["seen_at"] and time.monotonic() < deadline:
                self.assertIsNone(guardian.poll())
                time.sleep(0.05)
                current = self._status()
            self.assertIsNone(guardian.poll())
            self.assertTrue(_running(before["guardian_pid"]))
            self.assertNotEqual(before["seen_at"], current.get("seen_at"))
            self.assertTrue(current.get("ready"), current)
            self.assertTrue(self._healthy())
            self.assertFalse(self.control.frozen())

        duplicate_code, duplicate_output = self._command("serve", "--admin")
        self.assertNotEqual(0, duplicate_code)
        self.assertIn("HOST_ALREADY_RUNNING", duplicate_output)
        self.assertIsNone(guardian.poll())
        self.assertEqual(before["guardian_pid"], self._status()["guardian_pid"])
        self.assertEqual(initially_frozen, self.control.frozen())
        self.assertTrue(self._healthy())

        # No --admin here: restart must inherit the actual running topology.
        code, output = self._command("restart")
        self.assertEqual(0, code, output)
        receipt = json.loads(output)
        self.assertIs(receipt["ready"], True)
        self.assertIs(type(receipt["launcher_pid"]), int)
        self.pids.add(receipt["launcher_pid"])
        self.assertEqual(initially_frozen, receipt["frozen"])
        self.assertEqual(initially_frozen, self.control.frozen())
        after = self._status()
        deadline = time.monotonic() + 3
        while after.get("frozen") != initially_frozen and time.monotonic() < deadline:
            time.sleep(0.05)
            after = self._status()
        self.assertTrue(after.get("ready"), f"new guardian lost readiness: {after!r}")
        self.assertTrue(after.get("admin"), "restart dropped the original admin component")
        self.assertNotEqual(before["guardian_pid"], after["guardian_pid"])
        self.assertEqual(receipt["guardian_pid"], after["guardian_pid"])
        self.assertTrue(_belongs_to(self.jobs[-1], after["guardian_pid"]))
        self.assertIsInstance(before.get("boot_id"), str)
        self.assertIsInstance(after.get("boot_id"), str)
        self.assertNotEqual(before["boot_id"], after["boot_id"])
        self.assertEqual({"public", "admin", "health"}, set(after["components"]))
        self.assertTrue(set(before["components"].values()).isdisjoint(after["components"].values()))
        guardian.wait(timeout=5)
        self.assertFalse(_running(before["guardian_pid"]))
        for pid in set(before["components"].values()) | original_actual_pids:
            self.assertFalse(_running(pid), "an original owned child survived restart")
        self.assertEqual(initially_frozen, after["frozen"])
        self.assertEqual(initially_frozen, self.control.frozen())
        self.assertTrue(self._healthy())
        self.assertTrue(original_actual_pids.isdisjoint(self._component_pids(after)))
        # A second control command also resolves this new guardian, not stale status.
        stop_code, stop_output = self._command("stop")
        self.assertEqual(0, stop_code, stop_output)

    def test_restart_preserves_unfrozen_policy_and_replaces_ready_children(self) -> None:
        self._exercise_restart(False)

    def test_restart_preserves_frozen_policy_and_replaces_ready_children(self) -> None:
        self._exercise_restart(True)

    def tearDown(self) -> None:
        try:
            self._status()
            self.control.request_stop()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and any(_running(pid) for pid in self.pids):
                self._status()
                for process in self.processes:
                    process.poll()
                time.sleep(0.1)
        finally:
            # Only handles created by this fixture are closed; never scan or kill
            # other Python, host, database, browser, or worker processes.
            for job in self.jobs:
                job.close()
            for process in self.processes:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
            self.client.close()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and any(_running(pid) for pid in self.pids):
                time.sleep(0.05)
            remaining = sorted(pid for pid in self.pids if _running(pid))
            self.assertEqual([], remaining, "owned test process was not reaped")
            for port in self.ports:
                with socket.socket() as endpoint:
                    endpoint.settimeout(0.2)
                    self.assertNotEqual(0, endpoint.connect_ex(("127.0.0.1", port)))
            self.temporary.cleanup()
