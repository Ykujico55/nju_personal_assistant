"""Persistent local control files shared by the guardian and side-effect broker."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


class HostControl:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def frozen(self) -> bool:
        path = self.root / "freeze.json"
        if not path.exists():
            return False
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value.get("frozen") is not False
        except (OSError, ValueError):
            return True

    def _write(self, name: str, value: dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / ("." + uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        os.replace(temporary, self.root / name)

    def freeze(self) -> None:
        self._write("freeze.json", {"frozen": True})

    def resume(self) -> None:
        self._write("freeze.json", {"frozen": False})

    def request_stop(self) -> None:
        self._write("stop.json", {"stop": True})

    def stopping(self) -> bool:
        return (self.root / "stop.json").exists()
