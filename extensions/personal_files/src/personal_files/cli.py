"""Human-only local confirmation for a single, pre-reviewed delete plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from personal_assistant_sdk import RuntimeContext, WorkspaceConfigurationError, workspace_config

from personal_files.errors import FilesError
from personal_files.service import FileService


def _read_object(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(67585)
    if len(raw) > 67584:
        raise FilesError("CONFIG_INVALID")
    data: Any = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise FilesError("CONFIG_INVALID")
    return data


def _digest(value: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _snapshot(value: dict[str, Any]) -> tuple[dict[str, Any], str]:
    config, generation = value.get("config"), value.get("generation")
    if (set(value) != {"config", "digest", "generation"}
            or not isinstance(config, dict) or value["digest"] != _digest(config)
            or not isinstance(generation, str) or not 1 <= len(generation) <= 128):
        raise FilesError("CONFIG_INVALID")
    return config, generation


def _config(path: Path, workspace_state: Path | None = None) -> dict[str, Any]:
    data = _read_object(path)
    # Owner passes the plugin's applied snapshot, or its legacy standalone config.
    if "config" in data:
        data, _ = _snapshot(data)
    if "workspace_binding" not in data:
        if workspace_state is not None:
            raise FilesError("WORKSPACE_BINDING_INVALID")
        return data
    if workspace_state is None:
        raise FilesError("WORKSPACE_APPLIED_SNAPSHOT_REQUIRED")
    document, generation = _snapshot(_read_object(workspace_state))
    workspace = {**document, "revision": _digest({"generation": generation})[7:]}
    try:
        return workspace_config(RuntimeContext(
            protocol_version="1", extension_id="personal.files", extension_version="0.1.1",
            data_namespace="local-owner", non_secret_config=data, workspace=workspace,
        ))
    except WorkspaceConfigurationError as exc:
        raise FilesError(exc.code) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Confirm one local file deletion")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace-state", type=Path,
                        help="Authoritative applied instance workspace JSON; not an exported copy")
    sub = parser.add_subparsers(dest="command", required=True)
    delete = sub.add_parser("delete", help="Review and explicitly confirm a delete plan")
    delete.add_argument("plan_id")
    delete.add_argument("plan_hash")
    args = parser.parse_args(argv)
    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise FilesError("INTERACTIVE_TERMINAL_REQUIRED")
        service = FileService(_config(args.config, args.workspace_state))
        status = service.status(args.plan_id)
        if status["plan_hash"] != args.plan_hash:
            raise FilesError("PLAN_HASH_MISMATCH")
        if status["operation"] != "delete":
            raise FilesError("OPERATION_INVALID")
        if status["status"] != "PREVIEWED":
            raise FilesError("PLAN_ALREADY_USED")
        # JSON escaping avoids letting file contents inject terminal controls.
        print(json.dumps(status, ensure_ascii=True, indent=2))
        phrase = f"DELETE {status['root_key']}/{status['path']}"
        print("This permanently deletes ONE file; no directory recursion or automatic backup.")
        if input(f"Type exactly {phrase!r} to confirm: ") != phrase:
            raise FilesError("CONFIRMATION_DECLINED")
        # Re-read configuration and the exact old file hash after human confirmation.
        result = FileService(_config(args.config, args.workspace_state)).delete_confirmed(
            args.plan_id, args.plan_hash
        )
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result["status"] == "APPLIED" else 3
    except FilesError as exc:
        print(json.dumps({"ok": False, "error_code": exc.code}))
        return 2
    except (OSError, ValueError, EOFError):
        print(json.dumps({"ok": False, "error_code": "LOCAL_OPERATION_FAILED"}))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error_code": "INTERRUPTED_CHECK_PLAN_STATUS"}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
