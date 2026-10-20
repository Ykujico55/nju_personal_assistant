"""Local setup for a separately protected backup key. No key is written to a file."""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import sys
import warnings
from pathlib import Path

from personal_assistant.bootstrap import build_secret_store


async def setup() -> dict[str, object]:
    path = Path(__file__).resolve().parents[1] / "var/config/backup.json"
    if path.exists() or not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("use a local terminal; explicit rotation required for an existing key")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        value = getpass.getpass(
            "Existing 64-character recovery key (hidden), or Enter to generate: "
        )
    if not value:
        value = os.urandom(32).hex()
    if len(bytes.fromhex(value)) != 32:
        raise ValueError("invalid recovery key")
    store = build_secret_store()
    handle = await store.put(name="assistant.backup", kind="backup_key", value=value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump({"PA_BACKUP_SECRET_HANDLE": handle.id}, output)
    print(
        "Backup key retained by Windows Credential Manager; keep a separate offline recovery copy."
    )
    if (
        input("Type WRITE_OFFLINE_KEY to view the key in this local terminal, or Enter to skip: ")
        == "WRITE_OFFLINE_KEY"
    ):
        print(value)  # Explicit interactive owner export, never invoke through agent tools or logs.
    del value
    return {"ok": True, "handle": handle.id}


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(setup())))
    except (Exception, KeyboardInterrupt):
        print('{"ok":false,"code":"BACKUP_KEY_SETUP_FAILED"}')
        raise SystemExit(1) from None
