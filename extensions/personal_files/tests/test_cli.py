"""Deletion is explicitly confirmed locally, never via a model boolean."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from personal_files.cli import main
from personal_files.service import FileService


@pytest.fixture
def deletion(tmp_path: Path) -> tuple[Path, Path, dict, dict]:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "note.txt"
    target.write_bytes(b"test text")
    (tmp_path / "control").mkdir()
    config = {
        "state_directory": str(tmp_path / "control"),
        "roots": [{"key": "notes", "label": "Notes", "path": str(root), "mode": "read_write"}],
    }
    config_path = tmp_path / "files-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    p = FileService(config).preview(
        "delete",
        "notes",
        "note.txt",
        hashlib.sha256(b"test text").hexdigest(),
        idempotency_key="deletion-preview",
    )
    return target, config_path, config, p


def command(deletion: tuple) -> list[str]:
    _, config_path, _, p = deletion
    return ["--config", str(config_path), "delete", p["plan_id"], p["plan_hash"]]


def test_noninteractive_and_declined_deletion_do_not_mutate(deletion: tuple) -> None:
    target, _, _, _ = deletion
    with patch("sys.stdin.isatty", return_value=False):
        assert main(command(deletion)) == 2
    assert target.exists()
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
        patch("builtins.input", return_value="NO"),
    ):
        assert main(command(deletion)) == 2
    assert target.exists()


def test_confirmed_exact_plan_deletes_once(deletion: tuple) -> None:
    target, _, config, p = deletion
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
        patch("builtins.input", return_value="DELETE notes/note.txt"),
    ):
        assert main(command(deletion)) == 0
    assert not target.exists()
    assert FileService(config).status(p["plan_id"])["status"] == "APPLIED"


def test_edit_during_confirmation_is_rejected(deletion: tuple) -> None:
    target, _, _, _ = deletion

    def human_edit(_: str) -> str:
        target.write_bytes(b"changed while prompt open")
        return "DELETE notes/note.txt"

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
        patch("builtins.input", side_effect=human_edit),
    ):
        assert main(command(deletion)) == 2
    assert target.read_bytes() == b"changed while prompt open"


def test_expiry_during_confirmation_is_rejected(deletion: tuple) -> None:
    target, _, _, p = deletion
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
        patch("builtins.input", return_value="DELETE notes/note.txt"),
        patch("personal_files.service.time.time", return_value=p["expires_at"] + 1),
    ):
        assert main(command(deletion)) == 2
    assert target.exists()
