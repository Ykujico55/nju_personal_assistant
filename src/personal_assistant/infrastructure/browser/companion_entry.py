"""Entrypoint for the Desktop Companion process.

Run this module in the logged-in user's desktop session::

    python -m personal_assistant.infrastructure.browser.companion_entry

It binds a loopback port, prints one handshake line with the port and the
in-memory root capability, and then serves the JSON API.  The handshake line is
process-to-process data: redirect it to the host launcher, never to a log file.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from dataclasses import dataclass

from .companion import CAPABILITY_TTL_SECONDS, DesktopCompanion
from .driver import PlaywrightHeadedDriver, _snapshot_chrome_pids


@dataclass(frozen=True, slots=True)
class CompanionBoot:
    companion: DesktopCompanion
    test_mode: bool
    cdp_port: int | None


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_companion() -> CompanionBoot:
    test_mode = _env_flag("PA_COMPANION_TEST_MODE")
    cdp_port: int | None = None
    if test_mode:
        configured = int(os.getenv("PA_COMPANION_CDP_PORT", "0") or 0)
        cdp_port = configured or _free_loopback_port()
    options = {
        "test_mode": test_mode,
        "cdp_port": cdp_port,
        "allow_insecure_loopback_tls": _env_flag("PA_COMPANION_ALLOW_INSECURE_TLS"),
        "baseline_chrome_pids": _snapshot_chrome_pids() if test_mode else (),
    }
    companion = DesktopCompanion(
        driver_factory=lambda **kwargs: PlaywrightHeadedDriver(**kwargs),
        ttl_seconds=CAPABILITY_TTL_SECONDS,
        test_mode=test_mode,
        driver_options=options,
    )
    return CompanionBoot(companion=companion, test_mode=test_mode, cdp_port=cdp_port)


async def serve(port: int) -> None:
    import uvicorn

    from .companion_app import create_companion_app

    boot = build_companion()
    app = create_companion_app(boot.companion)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    bound_port = int(sock.getsockname()[1])
    handshake = {
        "type": "pa.desktop_companion.handshake",
        "port": bound_port,
        "capability": boot.companion.root_capability,
        "test_mode": boot.test_mode,
        "cdp_port": boot.cdp_port,
        "browser": "chromium",
    }
    print(json.dumps(handshake), flush=True)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=bound_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    try:
        await server.serve(sockets=[sock])
    finally:
        await boot.companion.shutdown()


def main() -> None:
    port = int(os.getenv("PA_COMPANION_PORT", "0") or 0)
    try:
        asyncio.run(serve(port))
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        sys.exit(130)


if __name__ == "__main__":
    main()


__all__ = ["CompanionBoot", "build_companion", "main", "serve"]
