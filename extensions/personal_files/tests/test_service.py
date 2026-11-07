"""Durable command and conflict regressions using only disposable local files."""

from __future__ import annotations

import hashlib
import multiprocessing
from pathlib import Path
from unittest.mock import patch

import pytest

from personal_files.errors import FilesError
from personal_files.service import FileService


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture
def config(tmp_path: Path) -> dict:
    root = tmp_path / "notes"
    root.mkdir()
    (root / "memo.txt").write_bytes(b"before\n")
    (tmp_path / "control").mkdir()
    return {
        "state_directory": str(tmp_path / "control"),
        "roots": [{"key": "notes", "label": "Notes", "path": str(root), "mode": "read_write"}],
    }


def plan(service: FileService, *, key: str = "p1", value: str = "after\n") -> dict:
    return service.preview(
        "write", "notes", "memo.txt", digest("before\n"), value, idempotency_key=key
    )


def apply(service: FileService, p: dict, key: str = "w1") -> dict:
    return service.write(p["plan_id"], p["plan_hash"], idempotency_key=key)


def test_preview_diff_and_receipt_survive_restart_without_reapplying(config: dict) -> None:
    s = FileService(config)
    p = plan(s)
    assert "-before" in p["diff"] and "+after" in p["diff"]
    assert s.read("notes", "memo.txt")["content"] == "before\n"
    assert plan(FileService(config)) == p
    receipt = apply(s, p)
    assert receipt["status"] == "APPLIED" and receipt["applied_sha256"] == digest("after\n")
    target = Path(config["roots"][0]["path"]) / "memo.txt"
    target.write_bytes(b"human later")
    assert apply(FileService(config), p) == receipt
    assert target.read_bytes() == b"human later"
    assert FileService(config).status(p["plan_id"])["receipt"] == receipt


def test_changed_target_and_plan_tampering_do_not_write(config: dict) -> None:
    s = FileService(config)
    p = plan(s)
    with pytest.raises(FilesError, match="PLAN_HASH_MISMATCH"):
        s.write(p["plan_id"], "0" * 64, idempotency_key="bad")
    target = Path(config["roots"][0]["path"]) / "memo.txt"
    target.write_bytes(b"human edit")
    with pytest.raises(FilesError, match="VERSION_CONFLICT"):
        apply(s, p)
    assert target.read_bytes() == b"human edit"
    with pytest.raises(FilesError, match="VERSION_CONFLICT"):
        apply(FileService(config), p)


def test_idempotency_keys_bind_payload_and_operation(config: dict) -> None:
    s = FileService(config)
    p = plan(s)
    with pytest.raises(FilesError, match="IDEMPOTENCY_CONFLICT"):
        plan(s, value="different")
    with pytest.raises(FilesError, match="IDEMPOTENCY_CONFLICT"):
        apply(s, p, key="p1")
    apply(s, p)
    with pytest.raises(FilesError, match="PLAN_ALREADY_USED"):
        apply(s, p, key="w2")


def test_create_requires_absence_and_read_paginates_without_state(config: dict) -> None:
    s = FileService(config)
    assert s.roots()["roots"][0]["key"] == "notes"
    assert s.read("notes", "memo.txt", offset=0, limit=3)["next_offset"] == 3
    assert not (Path(config["state_directory"]) / "files.sqlite3").exists()
    with pytest.raises(FilesError, match="VERSION_CONFLICT"):
        s.preview("write", "notes", "memo.txt", None, "new", idempotency_key="existing")
    p = s.preview("write", "notes", "new.txt", None, "new", idempotency_key="new")
    assert apply(s, p)["applied_sha256"] == digest("new")


def test_changed_config_and_expired_plan_cannot_apply(config: dict) -> None:
    s = FileService(config)
    p = plan(s)
    restricted = {**config, "roots": [{**config["roots"][0], "mode": "read_only"}]}
    with pytest.raises(FilesError, match="CONFIGURATION_CHANGED"):
        apply(FileService(restricted), p)
    with (
        patch("personal_files.service.time.time", return_value=p["expires_at"] + 1),
        pytest.raises(FilesError, match="PLAN_EXPIRED"),
    ):
        apply(s, p)
    assert s.read("notes", "memo.txt")["content"] == "before\n"


def test_delete_plan_cannot_run_through_write(config: dict) -> None:
    s = FileService(config)
    p = s.preview("delete", "notes", "memo.txt", digest("before\n"), idempotency_key="d1")
    assert p["requires_local_confirmation"] is True
    with pytest.raises(FilesError, match="LOCAL_CONFIRMATION_REQUIRED"):
        apply(s, p)
    assert s.read("notes", "memo.txt")["content"] == "before\n"


def test_crash_after_mutation_is_unknown_and_never_reapplies(config: dict) -> None:
    s = FileService(config)
    p = plan(s)
    with (
        patch.object(s, "_record_applied", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        apply(s, p)
    target = Path(config["roots"][0]["path"]) / "memo.txt"
    assert target.read_bytes() == b"after\n"
    target.write_bytes(b"human latest")
    assert apply(FileService(config), p)["status"] == "UNKNOWN"
    assert FileService(config).status(p["plan_id"])["status"] == "UNKNOWN"
    assert target.read_bytes() == b"human latest"


def _competing_write(config: dict, p: dict, key: str, gate: object, queue: object) -> None:
    try:
        gate.wait(10)
        result = apply(FileService(config), p, key)
        queue.put(result["status"])
    except FilesError as exc:
        queue.put(exc.code)


def test_processes_serialize_conflicting_versions(config: dict) -> None:
    s = FileService(config)
    p1, p2 = plan(s), plan(s, key="p2", value="other")
    context = multiprocessing.get_context("spawn")
    gate, queue = context.Event(), context.Queue()
    children = [
        context.Process(target=_competing_write, args=(config, p, k, gate, queue))
        for p, k in ((p1, "w1"), (p2, "w2"))
    ]
    try:
        for child in children:
            child.start()
        gate.set()
        results = sorted(queue.get(timeout=15) for _ in children)
        assert results.count("APPLIED") == 1
        assert set(results) <= {"APPLIED", "VERSION_CONFLICT", "TARGET_RESULT_UNKNOWN"}
        for child in children:
            child.join(10)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            if child.pid is not None:
                child.join(10)
                child.close()
        queue.close()
        queue.join_thread()


def test_delete_confirmed_rechecks_version_and_keeps_historical_receipt(config: dict) -> None:
    s = FileService(config)
    p = s.preview("delete", "notes", "memo.txt", digest("before\n"), idempotency_key="del")
    receipt = s.delete_confirmed(p["plan_id"], p["plan_hash"])
    assert receipt["status"] == "APPLIED" and receipt["operation"] == "delete"
    assert not (Path(config["roots"][0]["path"]) / "memo.txt").exists()
    assert FileService(config).status(p["plan_id"])["receipt"] == receipt


def test_diff_marks_missing_newline_instead_of_concatenating_sides(config: dict) -> None:
    target = Path(config["roots"][0]["path"]) / "memo.txt"
    target.write_bytes(b"before")
    s = FileService(config)
    p = s.preview(
        "write", "notes", "memo.txt", digest("before"), "after", idempotency_key="no-newline"
    )
    assert "-before\n\\ No newline at end of file\n+after\n" in p["diff"]
    assert target.read_bytes() == b"before"


def test_unknown_target_cannot_be_reissued_as_a_fresh_plan(config: dict) -> None:
    s = FileService(config)
    p = plan(s)
    other = plan(s, key="already-previewed", value="other")
    with (
        patch.object(s.access, "write", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        apply(s, p)
    assert s.status(p["plan_id"])["status"] == "UNKNOWN"
    with pytest.raises(FilesError, match="TARGET_RESULT_UNKNOWN"):
        plan(FileService(config), key="replacement-command")
    with pytest.raises(FilesError, match="TARGET_RESULT_UNKNOWN"):
        apply(FileService(config), other, key="replacement-write")
    assert s.read("notes", "memo.txt")["content"] == "before\n"


def test_directory_plan_creates_one_level_and_receipt_does_not_touch_children(config: dict) -> None:
    s = FileService(config)
    p = s.preview("mkdir", "notes", "materials", None, idempotency_key="mkdir-preview")
    root = Path(config["roots"][0]["path"])
    assert not (root / "materials").exists()
    result = apply(s, p, "mkdir-write")
    assert result["status"] == "APPLIED" and result["operation"] == "mkdir"
    (root / "materials" / "keep.txt").write_text("human content", encoding="utf-8")
    assert apply(FileService(config), p, "mkdir-write") == result
    assert (root / "materials" / "keep.txt").read_text() == "human content"
    new = s.preview(
        "write", "notes", "materials/summary.md", None, "summary", idempotency_key="inside-preview"
    )
    assert apply(s, new, "inside-write")["status"] == "APPLIED"
    with pytest.raises(FilesError, match="VERSION_CONFLICT"):
        s.preview("mkdir", "notes", "materials", None, idempotency_key="already-there")


def test_directory_plan_rejects_content_and_old_file_hash(config: dict) -> None:
    s = FileService(config)
    for expected, content in [(None, "body"), (digest("before"), None)]:
        with pytest.raises(FilesError, match="DIRECTORY_INPUT_INVALID"):
            s.preview("mkdir", "notes", "folder", expected, content, idempotency_key="bad-mkdir")
    assert not (Path(config["roots"][0]["path"]) / "folder").exists()
