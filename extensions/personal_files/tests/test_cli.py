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


@pytest.mark.parametrize("change_during_confirmation", [False, True])
def test_workspace_delete_rereads_authoritative_snapshots(
    deletion, tmp_path, change_during_confirmation
):
    from personal_assistant_sdk import RuntimeContext, workspace_config

    target, config_path, config, _ = deletion
    snapshot_path = tmp_path / "workspace-applied.json"
    document = {"workspace_id": "instance", "roots": config["roots"]}
    def digest(value):
        return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    snapshot = {"config": document, "digest": digest(document), "generation": "1" * 32}
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    binding = {"workspace_binding": "instance", "state_directory": config["state_directory"]}
    config_path.write_text(json.dumps(binding), encoding="utf-8")
    revision = digest({"generation": snapshot["generation"]})[7:]
    effective = workspace_config(RuntimeContext(
        "1", "personal.files", "0.1.1", "synthetic", non_secret_config=binding,
        workspace={**document, "revision": revision},
    ))
    plan = FileService(effective).preview("delete", "notes", "note.txt",
        hashlib.sha256(b"test text").hexdigest(), idempotency_key="workspace-delete")
    def confirm(_):
        if change_during_confirmation:
            snapshot["generation"] = "2" * 32
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        return "DELETE notes/note.txt"
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
        patch("builtins.input", side_effect=confirm),
    ):
        result = main(["--config", str(config_path), "--workspace-state", str(snapshot_path),
            "delete", plan["plan_id"], plan["plan_hash"]])
    assert result == (2 if change_during_confirmation else 0)
    assert target.exists() is change_during_confirmation
