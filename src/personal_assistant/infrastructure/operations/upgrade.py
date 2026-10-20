"""Reviewable offline wheel release plans and local atomic version pointers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from personal_assistant.core.records import fingerprint
from personal_assistant.domain import ValidationError


def plan_release(wheels: list[Path]) -> dict[str, Any]:
    if not wheels or len(wheels) > 100 or len({p.name for p in wheels}) != len(wheels):
        raise ValidationError("select one complete, uniquely named offline wheel set")
    items = []
    for path in sorted(wheels):
        if not path.is_file() or path.suffix != ".whl" or path.is_symlink():
            raise ValidationError("release materials must be fixed wheel files")
        items.append(
            {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        )
    return {"wheels": items, "hash": fingerprint(items)}


def confirm_release(plan: dict[str, Any], accepted_hash: str) -> None:
    if fingerprint(plan["wheels"]) != plan.get("hash") or accepted_hash != plan["hash"]:
        raise ValidationError("release requires the exact reviewed material hash")
    for item in plan["wheels"]:
        if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValidationError("wheel changed after review")


def stage_release(plan: dict[str, Any], accepted_hash: str, target: Path) -> list[Path]:
    """Copy exact reviewed bytes once; installation uses only the owned snapshot."""
    confirm_release(plan, accepted_hash)
    prepared = []
    for item in plan["wheels"]:
        source = Path(item["path"])
        if source.is_symlink() or not source.is_file():
            raise ValidationError("release material changed")
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValidationError("release material changed before staging")
        prepared.append((source.name, data))
    target.mkdir(parents=True, exist_ok=False)
    result = []
    for name, data in prepared:
        path = target / name
        path.write_bytes(data)
        result.append(path)
    return result


def select_python(release_root: Path, pointer: dict[str, Any]) -> Path:
    candidate = Path(pointer.get("python", "")).resolve()
    root = release_root.resolve()
    if (
        not candidate.is_relative_to(root)
        or candidate.name.lower() != "python.exe"
        or not candidate.is_file()
    ):
        raise ValueError("release Python must remain inside its managed environment")
    return candidate


def activate_pointer(
    root: Path, *, python: Path, release_hash: str, backup_id: str
) -> dict[str, Any]:
    candidate = select_python(root, {"python": str(python)})
    current = root / "current.json"
    old = json.loads(current.read_text()) if current.exists() else None
    if old:
        (root / "previous.json").write_text(json.dumps(old), encoding="utf-8")
    value = {"python": str(candidate), "release_hash": release_hash, "backup_id": backup_id}
    temporary = root / ("." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, current)
    return value
