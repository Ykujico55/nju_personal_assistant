from unittest.mock import patch

import pytest
from personal_assistant_sdk import RuntimeContext, workspace_config

from personal_files.errors import FilesError
from personal_files.service import FileService
from personal_files.worker import PersonalFilesExtension


def runtime(tmp_path, revision="rev-1", mode="read_write"):
    for name in ("root", "state"):
        (tmp_path / name).mkdir(exist_ok=True)
    return RuntimeContext(
        "1",
        "personal.files",
        "test",
        "ext_files",
        non_secret_config={
            "workspace_binding": "instance",
            "state_directory": str(tmp_path / "state"),
        },
        workspace={
            "workspace_id": "instance",
            "revision": revision,
            "roots": [
                {"key": "notes", "label": "Notes", "path": str(tmp_path / "root"), "mode": mode}
            ],
        },
    )


@pytest.mark.asyncio
async def test_worker_consumes_bound_roots_and_mode(tmp_path):
    worker = PersonalFilesExtension()
    await worker.initialize(runtime(tmp_path, mode="read_only"))
    assert worker._files().roots()["roots"][0]["mode"] == "read_only"
    with pytest.raises(FilesError, match="READ_ONLY"):
        worker._files().preview("write", "notes", "a.txt", None, "text", idempotency_key="p")
    await worker.shutdown()


def test_new_workspace_revision_invalidates_plan_even_after_switching_back(tmp_path):
    old = FileService(workspace_config(runtime(tmp_path)))
    plan = old.preview("write", "notes", "a.txt", None, "text", idempotency_key="p")
    current = FileService(workspace_config(runtime(tmp_path, "rev-3")))
    with pytest.raises(FilesError, match="CONFIGURATION_CHANGED"):
        current.write(plan["plan_id"], plan["plan_hash"], idempotency_key="w")
    assert not (tmp_path / "root" / "a.txt").exists()


def test_unknown_survives_workspace_revision_without_replay_or_new_plan(tmp_path):
    old = FileService(workspace_config(runtime(tmp_path)))
    plan = old.preview("write", "notes", "a.txt", None, "text", idempotency_key="p")
    with (
        patch.object(old, "_record_applied", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        old.write(plan["plan_id"], plan["plan_hash"], idempotency_key="w")
    target = tmp_path / "root" / "a.txt"
    target.write_text("human", encoding="utf-8")
    current = FileService(workspace_config(runtime(tmp_path, "rev-3")))
    assert (
        current.write(plan["plan_id"], plan["plan_hash"], idempotency_key="w")["status"]
        == "UNKNOWN"
    )
    import hashlib

    with pytest.raises(FilesError, match="TARGET_RESULT_UNKNOWN"):
        current.preview(
            "write",
            "notes",
            "a.txt",
            hashlib.sha256(b"human").hexdigest(),
            "overwrite",
            idempotency_key="new",
        )
    assert target.read_text() == "human"


def test_direct_config_cannot_fall_back_to_legacy_roots_in_bound_mode(tmp_path):
    config = workspace_config(runtime(tmp_path))
    config["workspace_binding"] = "instance"
    with pytest.raises(FilesError, match="CONFIG_INVALID"):
        FileService(config)
