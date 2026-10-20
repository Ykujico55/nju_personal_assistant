from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from personal_assistant.infrastructure.operations.host import load_profile
from personal_assistant.infrastructure.platform.windows.operations import WindowsServiceManager


async def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install-startup", "uninstall-startup"])
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    load_profile(args.profile)
    root = Path(__file__).resolve().parents[1]
    manager = WindowsServiceManager(cwd=root)
    if args.action == "install-startup":
        await manager.install(
            "assistant",
            (
                sys.executable,
                "-Xutf8",
                "-m",
                "personal_assistant.host_entry",
                "serve",
                "--profile",
                str(args.profile.resolve()),
            ),
        )
    else:
        await manager.uninstall("assistant")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        print("STARTUP_OPERATION_FAILED")
        raise SystemExit(1) from None
