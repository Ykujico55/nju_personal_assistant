"""Six-hour consistent database packages with incremental encrypted artifact objects."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from personal_assistant.infrastructure.operations.recovery import RecoveryVault, retained_points

POINT = re.compile(r"[0-9]{8}T[0-9]{6}_[0-9a-f]{32}\.pa-backup")
DIGEST = re.compile(r"[0-9a-f]{64}")


class BackupCoordinator:
    def __init__(
        self,
        root: Path,
        vault: RecoveryVault,
        export: Callable[[], Awaitable[dict[str, Any]]],
        *,
        offsite: Path | None = None,
    ):
        self.root, self.vault, self.export = root.resolve(), vault, export
        self.offsite = offsite.resolve() if offsite else None
        if self.offsite and (self.offsite == self.root or self.root in self.offsite.parents):
            raise ValueError("offsite backup must use a separate configured directory")
        self.lock = asyncio.Lock()

    def index(self) -> list[dict[str, str]]:
        path = self.root / "index.json"
        if not path.exists():
            return []
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list) or any(
            set(r) != {"name", "created_at", "sha256"}
            or not POINT.fullmatch(r["name"])
            or not DIGEST.fullmatch(r["sha256"])
            for r in value
        ):
            raise ValueError("backup index is invalid")
        return value

    def _write_index(self, points: list[dict[str, str]]) -> None:
        temporary = self.root / ("." + uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(points, indent=2), encoding="utf-8")
        os.replace(temporary, self.root / "index.json")

    async def tick(
        self, *, now: datetime | None = None, force: bool = False
    ) -> dict[str, str] | None:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("backup time must be aware")
        async with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            points = self.index()
            if (
                not force
                and points
                and current - max(datetime.fromisoformat(p["created_at"]) for p in points)
                < timedelta(hours=6)
            ):
                return None
            snapshot = await self.export()
            snapshot = copy.deepcopy(snapshot)
            objects = self.root / "objects"
            objects.mkdir(exist_ok=True)
            for key, artifact in snapshot["artifacts"].items():
                digest = artifact["sha256"]
                if not DIGEST.fullmatch(digest):
                    raise ValueError("invalid artifact digest")
                path = objects / (digest + ".pa-blob")
                if not path.exists():
                    sealed = await self.vault.seal(
                        {"format": 1, "tables": {}, "artifacts": {key: artifact}}
                    )
                    with path.open("xb") as output:
                        output.write(sealed)
                else:
                    # Authentication and content checks precede reusing a stored object.
                    previous = await self.vault.open(path.read_bytes())
                    if previous.get("artifacts", {}).get(key) != artifact:
                        raise ValueError("stored artifact object changed")
                snapshot["artifacts"][key] = {"sha256": digest, "object": digest}
            snapshot["created_at"] = current.isoformat()
            encrypted = await self.vault.seal(snapshot)
            name = current.strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex + ".pa-backup"
            path = self.root / name
            with path.open("xb") as output:
                output.write(encrypted)
            if self.offsite:
                self.offsite.mkdir(parents=True, exist_ok=True)
                (self.offsite / "objects").mkdir(exist_ok=True)
                for artifact in snapshot["artifacts"].values():
                    source = objects / (artifact["object"] + ".pa-blob")
                    target = self.offsite / "objects" / source.name
                    if not target.exists():
                        shutil.copyfile(source, target)
                    elif (
                        hashlib.sha256(target.read_bytes()).digest()
                        != hashlib.sha256(source.read_bytes()).digest()
                    ):
                        raise ValueError("offsite encrypted object mismatch")
                shutil.copyfile(path, self.offsite / name)
            receipt = {
                "name": name,
                "created_at": current.isoformat(),
                "sha256": hashlib.sha256(encrypted).hexdigest(),
            }
            points.append(receipt)
            self._write_index(points)
            await self.prune()
            return receipt

    async def read(self, name: str) -> dict[str, Any]:
        if not POINT.fullmatch(name):
            raise ValueError("backup id is invalid")
        snapshot = await self.vault.open((self.root / name).read_bytes())
        for key, artifact in list(snapshot["artifacts"].items()):
            digest = artifact.get("object")
            if (
                not isinstance(digest, str)
                or not DIGEST.fullmatch(digest)
                or digest != artifact["sha256"]
            ):
                raise ValueError("invalid encrypted artifact reference")
            decoded = await self.vault.open(
                (self.root / "objects" / (digest + ".pa-blob")).read_bytes()
            )
            snapshot["artifacts"][key] = decoded["artifacts"][key]
        return snapshot

    async def prune(self) -> None:
        points = self.index()
        keep = retained_points({p["name"]: datetime.fromisoformat(p["created_at"]) for p in points})
        objects: set[str] = set()
        for point in points:
            if point["name"] in keep:
                snapshot = await self.vault.open((self.root / point["name"]).read_bytes())
                objects.update(a["object"] for a in snapshot["artifacts"].values())
        for point in points:
            if point["name"] not in keep:
                (self.root / point["name"]).unlink()
        for path in (self.root / "objects").glob("*.pa-blob"):
            if DIGEST.fullmatch(path.stem) and path.stem not in objects:
                path.unlink()
        self._write_index([p for p in points if p["name"] in keep])
