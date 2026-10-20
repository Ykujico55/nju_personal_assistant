"""Metadata-only local operation logs, bounded rotation and explicit retention."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path


class MetadataLogger:
    def __init__(self, root: Path, *, max_bytes: int = 1024 * 1024, copies: int = 5):
        self.root, self.max_bytes, self.copies = root.resolve(), max_bytes, copies
        if max_bytes < 100 or not 1 <= copies <= 20:
            raise ValueError("invalid log bounds")
        self.root.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **values: object) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.]{1,63}", event):
            raise ValueError("invalid log event")
        record: dict[str, object] = {"event": event, "at": datetime.now(UTC).isoformat()}
        for key in {
            "component",
            "pid",
            "code",
            "restart_count",
            "free_bytes",
            "frozen",
            "backup_age_seconds",
        }:
            value = values.get(key)
            if (
                isinstance(value, bool)
                or type(value) is int
                or isinstance(value, str)
                and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value)
            ):
                record[key] = value
        path = self.root / "operations.jsonl"
        if path.exists() and path.stat().st_size >= self.max_bytes:
            for index in range(self.copies, 0, -1):
                target = self.root / f"operations.jsonl.{index}"
                previous = path if index == 1 else self.root / f"operations.jsonl.{index - 1}"
                if previous.exists():
                    previous.replace(target)
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")

    def prune(self, *, now: datetime | None = None) -> None:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=30)
        for index in range(1, self.copies + 1):
            path = self.root / f"operations.jsonl.{index}"
            if path.exists() and datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                path.unlink()
