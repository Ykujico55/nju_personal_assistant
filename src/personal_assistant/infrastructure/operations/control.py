"""Persistent local control with cross-process freeze ownership checks."""

from __future__ import annotations

import importlib
import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4


class HostControl:
    def __init__(self, root: Path):
        self.root = root.resolve()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        stream = (self.root / "control.lock").open("a+b")
        try:
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            deadline = time.monotonic() + 5
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl = importlib.import_module("fcntl")
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError("host control is busy") from None
                    time.sleep(0.02)
            yield
        finally:
            stream.close()

    def read(self, name: str) -> dict[str, Any]:
        try:
            with (self.root / name).open("rb") as stream:
                raw = stream.read(16385)
            if len(raw) > 16384:
                return {}
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def frozen(self) -> bool:
        path = self.root / "freeze.json"
        return path.exists() and self.read("freeze.json").get("frozen") is not False

    def _write(self, name: str, value: dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / ("." + uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps(value), encoding="utf-8")
            deadline = time.monotonic() + 2
            while True:
                try:
                    os.replace(temporary, self.root / name)
                    break
                except PermissionError as exc:
                    # Windows readers may briefly omit FILE_SHARE_DELETE. Keep
                    # replacement atomic; a persistent denial remains an error.
                    if (
                        os.name != "nt"
                        or getattr(exc, "winerror", None) not in {5, 32, 33}
                        or time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(0.01)
        finally:
            temporary.unlink(missing_ok=True)

    def _freeze(self, owner: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", owner) is None:
            raise ValueError("invalid freeze owner")
        revision = uuid4().hex
        self._write("freeze.json", {"frozen": True, "revision": revision, "owner": owner})
        return revision

    def freeze(self, *, owner: str = "manual", preserve_existing: bool = False) -> None:
        with self._locked():
            if preserve_existing and self.frozen():
                return
            self._freeze(owner)

    def resume(self) -> None:
        with self._locked():
            self._write(
                "freeze.json", {"frozen": False, "revision": uuid4().hex, "owner": "manual"}
            )

    def begin_restart(self, owner: str) -> str | None:
        with self._locked():
            return None if self.frozen() else self._freeze(owner)

    def resume_restart(self, ticket: str | None, owner: str) -> bool:
        with self._locked():
            current = self.read("freeze.json")
            if (
                ticket is None
                or current.get("revision") != ticket
                or current.get("owner") != owner
                or current.get("frozen") is not True
            ):
                return False
            self._write("freeze.json", {"frozen": False, "revision": uuid4().hex, "owner": owner})
            return True

    def request_stop(self, *, boot_id: str | None = None) -> None:
        if boot_id is not None and re.fullmatch(r"[0-9a-f]{32}", boot_id) is None:
            raise ValueError("invalid guardian boot identity")
        with self._locked():
            self._write("stop.json", {"stop": True, "boot_id": boot_id})

    def stopping(self, boot_id: str | None = None) -> bool:
        if not (self.root / "stop.json").exists():
            return False
        requested = self.read("stop.json").get("boot_id")
        return boot_id is None or requested is None or requested == boot_id

    def prepare_start(self, boot_id: str) -> None:
        with self._locked():
            # A timeout may already have requested this exact new boot to stop.
            if self.read("stop.json").get("boot_id") != boot_id:
                (self.root / "stop.json").unlink(missing_ok=True)
