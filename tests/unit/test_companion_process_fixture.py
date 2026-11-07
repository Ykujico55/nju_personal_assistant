"""Companion test startup reserves CDP and always reaps a failed fake child."""

from __future__ import annotations

import asyncio
import errno
import json
import socket
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support import companion_process


def _port_is_reserved(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as contender:
        # On Windows, ordinary bind alone is not sufficient to exclude this.
        contender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            contender.bind(("127.0.0.1", port))
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EADDRINUSE, 10013, 10048}:
                raise
            return True
        return False


def _assert_port_released(port: int) -> None:
    assert 0 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", port))


class _FakeProcess:
    def __init__(self, mode: str = "valid", *, pause_spawn: bool = False) -> None:
        self.mode = mode
        self.pause_spawn = pause_spawn
        self.returncode: int | None = None
        self.stdout = SimpleNamespace(readline=self.readline)
        self.cdp_port = 0
        self.rpc_port = 0
        self.reservations: list[tuple[str, bool]] = []
        self.spawn_entered = asyncio.Event()
        self.spawn_release = asyncio.Event()
        self.read_entered = asyncio.Event()
        self.read_release = asyncio.Event()
        self.kills = 0
        self.waits = 0

    async def create(self, *args: Any, **kwargs: Any) -> _FakeProcess:
        self.cdp_port = int(kwargs["env"]["PA_COMPANION_CDP_PORT"])
        self.rpc_port = self.cdp_port % 65535 + 1
        self.reservations.append(("spawn", _port_is_reserved(self.cdp_port)))
        self.spawn_entered.set()
        if self.pause_spawn:
            # A child exists, but its Process handle has not reached the caller.
            await self.spawn_release.wait()
        return self

    async def readline(self) -> bytes:
        self.reservations.append(("handshake", _port_is_reserved(self.cdp_port)))
        self.read_entered.set()
        if self.mode == "pending":
            await self.read_release.wait()
        if self.mode == "timeout":
            raise TimeoutError("synthetic handshake deadline")
        if self.mode == "bad_json":
            return b"{broken-json\n"
        value = {
            "type": "pa.desktop_companion.handshake",
            "port": self.cdp_port if self.mode == "same_port" else self.rpc_port,
            "capability": "fixture-capability-not-a-secret",
            "cdp_port": self.rpc_port if self.mode == "wrong_cdp" else self.cdp_port,
            "test_mode": True,
        }
        return (json.dumps(value) + "\n").encode()

    def kill(self) -> None:
        self.kills += 1
        self.returncode = -9

    def terminate(self) -> None:
        self.returncode = -15

    async def wait(self) -> int:
        self.waits += 1
        assert self.returncode is not None, "wait called before fake child was stopped"
        return self.returncode


@pytest.mark.parametrize("explicit_port", [False, True])
async def test_cdp_port_reserved_until_valid_handshake_then_released(
    monkeypatch: pytest.MonkeyPatch, explicit_port: bool
) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", process.create)
    requested = companion_process.free_loopback_port() if explicit_port else None
    result = await companion_process.start_companion(cdp_port=requested)
    try:
        assert cast(object, result.process) is process
        assert result.cdp_port == process.cdp_port
        if requested is not None:
            assert result.cdp_port == requested
        assert process.reservations == [("spawn", True), ("handshake", True)]
        _assert_port_released(process.cdp_port)
        assert process.kills == process.waits == 0
    finally:
        await result.stop()


@pytest.mark.parametrize("mode", ["bad_json", "same_port", "wrong_cdp"])
async def test_bad_handshake_reaps_child_and_releases_cdp(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    process = _FakeProcess(mode)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", process.create)
    with pytest.raises(RuntimeError):
        await companion_process.start_companion()
    assert process.kills == 1 and process.waits == 1
    _assert_port_released(process.cdp_port)


async def test_handshake_timeout_reaps_child_and_releases_cdp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess("timeout")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", process.create)
    with pytest.raises(RuntimeError, match="handshake"):
        await companion_process.start_companion()
    assert process.kills == 1 and process.waits == 1
    _assert_port_released(process.cdp_port)


@pytest.mark.parametrize("during_spawn", [False, True])
async def test_cancelled_startup_reaps_child_and_releases_cdp(
    monkeypatch: pytest.MonkeyPatch, during_spawn: bool
) -> None:
    process = _FakeProcess("pending", pause_spawn=during_spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", process.create)
    task = asyncio.create_task(companion_process.start_companion())
    try:
        boundary = process.spawn_entered if during_spawn else process.read_entered
        await asyncio.wait_for(boundary.wait(), timeout=5)
        task.cancel()
        process.spawn_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.kills == 1 and process.waits == 1
        _assert_port_released(process.cdp_port)
    finally:
        process.spawn_release.set()
        process.read_release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
