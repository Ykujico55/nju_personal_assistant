"""Launch the Desktop Companion as a real subprocess for integration tests."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

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


async def start_companion(
    *,
    test_mode: bool = True,
    allow_insecure_tls: bool = True,
    cdp_port: int | None = None,
) -> CompanionProcess:
    env = dict(os.environ)
    env["PA_COMPANION_PORT"] = "0"
    if test_mode:
        env["PA_COMPANION_TEST_MODE"] = "1"
        env["PA_COMPANION_CDP_PORT"] = str(cdp_port or free_loopback_port())
    if allow_insecure_tls:
        env["PA_COMPANION_ALLOW_INSECURE_TLS"] = "1"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "personal_assistant.infrastructure.browser.companion_entry",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert process.stdout is not None
    try:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=HANDSHAKE_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("the companion did not print a handshake") from None
    if not line:
        code = await process.wait()
        raise RuntimeError(f"the companion exited with code {code} before its handshake")
    handshake = json.loads(line.decode("utf-8").strip())
    if handshake.get("type") != "pa.desktop_companion.handshake":
        process.kill()
        await process.wait()
        raise RuntimeError("unexpected companion handshake")
    return CompanionProcess(
        process=process,
        port=int(handshake["port"]),
        capability=str(handshake["capability"]),
        cdp_port=handshake.get("cdp_port"),
        test_mode=bool(handshake.get("test_mode")),
    )


def pid_alive(pid: int) -> bool:
    """Windows-only liveness probe used for process-reaping assertions."""

    if sys.platform != "win32":
        return False
    try:
        output = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return False
    return str(pid) in output


__all__ = ["CompanionProcess", "free_loopback_port", "pid_alive", "start_companion"]
