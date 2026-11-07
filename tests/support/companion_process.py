"""Launch the Desktop Companion as a real subprocess for integration tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from personal_assistant.core.models.cleanup import run_cleanup

REPO_ROOT = Path(__file__).resolve().parents[2]
HANDSHAKE_TIMEOUT_SECONDS = 30.0


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class CompanionProcess:
    process: asyncio.subprocess.Process
    port: int
    capability: str
    cdp_port: int | None
    test_mode: bool

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        if self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=8)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()


async def _reap_failed_start(spawning: asyncio.Task[asyncio.subprocess.Process]) -> None:
    # The child may exist before create_subprocess_exec returns its handle.
    process = await spawning
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    await process.wait()


async def start_companion(
    *,
    test_mode: bool = True,
    allow_insecure_tls: bool = True,
    cdp_port: int | None = None,
) -> CompanionProcess:
    env = dict(os.environ)
    env["PA_COMPANION_PORT"] = "0"
    env["PA_COMPANION_TEST_MODE"] = "1" if test_mode else "0"
    env["PA_COMPANION_ALLOW_INSECURE_TLS"] = "1" if allow_insecure_tls else "0"
    env.pop("PA_COMPANION_CDP_PORT", None)
    reservation: socket.socket | None = None
    reserved_port: int | None = None
    spawning: asyncio.Task[asyncio.subprocess.Process] | None = None
    try:
        if test_mode:
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if sys.platform == "win32":
                reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            # Keep CDP unavailable to RPC bind(0), including SO_REUSEADDR on Windows.
            # Chromium starts only after this helper returns and a session is created.
            reservation.bind(("127.0.0.1", cdp_port or 0))
            reserved_port = int(reservation.getsockname()[1])
            env["PA_COMPANION_CDP_PORT"] = str(reserved_port)
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "personal_assistant.infrastructure.browser.companion_entry",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(REPO_ROOT),
                env=env,
            )
        )
        process = await asyncio.shield(spawning)
        assert process.stdout is not None
        line = await asyncio.wait_for(process.stdout.readline(), timeout=HANDSHAKE_TIMEOUT_SECONDS)
        if not line:
            raise RuntimeError("the companion exited before its handshake")
        try:
            handshake = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise RuntimeError("invalid companion handshake") from None
        if (
            not isinstance(handshake, dict)
            or handshake.get("type") != "pa.desktop_companion.handshake"
        ):
            raise RuntimeError("unexpected companion handshake")
        port = handshake.get("port")
        capability = handshake.get("capability")
        if (
            type(port) is not int
            or not 0 < port < 65536
            or not isinstance(capability, str)
            or not capability
            or handshake.get("test_mode") is not test_mode
            or handshake.get("cdp_port") != reserved_port
            or port == reserved_port
        ):
            raise RuntimeError("invalid companion handshake ports or mode")
        return CompanionProcess(
            process=process,
            port=port,
            capability=capability,
            cdp_port=reserved_port,
            test_mode=test_mode,
        )
    except BaseException as error:
        if spawning is not None:
            await run_cleanup(_reap_failed_start(spawning))
        if isinstance(error, TimeoutError):
            raise RuntimeError("the companion did not print a handshake") from None
        raise
    finally:
        if reservation is not None:
            reservation.close()


def pid_alive(pid: int) -> bool:
    """Windows-only liveness probe used for process-reaping assertions."""

    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=False,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as error:
        raise RuntimeError("the browser liveness probe is unavailable") from error
    if result.returncode or not isinstance(result.stdout, bytes):
        raise RuntimeError("the browser liveness probe failed")
    # Process names and localized messages use the native Windows codepage.
    # The PID CSV column is ASCII; match the exact number without decoding it.
    observed = re.findall(rb'^\s*"[^"]*","([0-9]+)"', result.stdout, flags=re.MULTILINE)
    return str(pid).encode("ascii") in observed



__all__ = ["CompanionProcess", "free_loopback_port", "pid_alive", "start_companion"]
