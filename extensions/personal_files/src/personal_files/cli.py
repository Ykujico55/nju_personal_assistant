"""Human-only local confirmation for a single, pre-reviewed delete plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from personal_files.errors import FilesError
from personal_files.service import FileService


def _config(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 65536:
        raise FilesError("CONFIG_INVALID")
    data: Any = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise FilesError("CONFIG_INVALID")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Confirm one local file deletion")
    parser.add_argument("--config", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    delete = sub.add_parser("delete", help="Review and explicitly confirm a delete plan")
    delete.add_argument("plan_id")
    delete.add_argument("plan_hash")
    args = parser.parse_args(argv)
    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise FilesError("INTERACTIVE_TERMINAL_REQUIRED")
        service = FileService(_config(args.config))
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
        result = FileService(_config(args.config)).delete_confirmed(args.plan_id, args.plan_hash)
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
