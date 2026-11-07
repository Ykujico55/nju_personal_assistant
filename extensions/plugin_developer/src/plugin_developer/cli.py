"""Interactive local owner bridge; never exposed as Agent tools."""
from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .control import ControlError, ControlStore
from .validation import candidate_digest, static_review
from .workspace import Workspace


def _require_terminal() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ControlError("INTERACTIVE_TERMINAL_REQUIRED")


def _confirm(message: str) -> None:
    print(message)
    if input("Type YES to confirm, anything else cancels: ").strip() != "YES":
        raise ControlError("CONFIRMATION_DECLINED")


def _admin_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        if (
            parsed.scheme != "http" or not address.is_loopback or parsed.port is None
            or not 1 <= parsed.port <= 65535 or parsed.path not in {"", "/"}
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
        ):
            raise ValueError()
    except ValueError:
        raise ControlError("ADMIN_LOOPBACK_REQUIRED") from None
    return value.rstrip("/")


def _host_python(value: str | None) -> str:
    selected = Path(value) if value is not None else Path(sys.executable)
    if not selected.is_absolute() or not selected.is_file():
        raise ControlError("HOST_PYTHON_REQUIRED")
    return str(selected)


def _host(
    python: str, admin_url: str, arguments: list[str],
) -> int:
    command = [python, "-Xutf8", "-B", "-m", "personal_assistant.cli.main",
               "--admin-url", admin_url, "extension", *arguments]
    try:
        # Inherit the owner's terminal: the original installer performs its own
        # exact-material confirmation.  Never add --yes or capture its nonce.
        return subprocess.run(command, check=False).returncode
    except OSError:
        raise ControlError("HOST_COMMAND_FAILED") from None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Local plugin development approval")
    result.add_argument("--workspace-root", required=True, type=Path)
    commands = result.add_subparsers(dest="command", required=True)
    approve = commands.add_parser("approve-tests")
    approve.add_argument("candidate_id")
    approve.add_argument("--iterative", action="store_true")
    approve.add_argument("--digest")
    status = commands.add_parser("status")
    status.add_argument("candidate_id")
    install = commands.add_parser("install")
    install.add_argument("candidate_id")
    install.add_argument("--digest")
    install.add_argument("--upgrade", action="store_true")
    install.add_argument("--enable", action="store_true")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("extension_id")
    for item in (install, rollback):
        item.add_argument("--host-python")
        item.add_argument("--admin-url", default="http://127.0.0.1:8001")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        _require_terminal()
        workspace = Workspace(args.workspace_root)
        control = ControlStore(workspace)
        if args.command == "status":
            print(json.dumps(control.status(args.candidate_id), ensure_ascii=False, indent=2))
            return 0
        if args.command == "approve-tests":
            review = workspace.review(args.candidate_id)
            if args.digest is not None and review["candidate_hash"] != args.digest:
                raise ControlError("CANDIDATE_CHANGED")
            static = static_review(workspace.candidate_path(args.candidate_id))
            print(json.dumps({"review": review, "static": static}, ensure_ascii=False, indent=2))
            if static.get("ok") is not True:
                raise ControlError("STATIC_REVIEW_FAILED")
            mode = (
                "Trust generated code edits for 30 minutes / at most 5 runs; changes to "
                "manifest, dependency lock or pyproject require another approval."
                if args.iterative else "Allow one run of exactly the displayed candidate hash."
            )
            _confirm(
                mode + "\nValidation executes trusted code as this Windows user. "
                "The workspace and subprocess are not a malicious-code sandbox."
            )
            grant = control.grant(
                args.candidate_id, review["candidate_hash"],
                ttl_seconds=1800 if args.iterative else 900,
                allow_edits=args.iterative, max_runs=5 if args.iterative else 1,
            )
            print(json.dumps({"ok": True, "grant": grant}, ensure_ascii=False))
            return 0
        admin_url = _admin_url(args.admin_url)
        python = _host_python(args.host_python)
        if args.command == "rollback":
            _confirm("Roll back installed extension " + args.extension_id + "?")
            return _host(python, admin_url, ["rollback", args.extension_id])
        review = workspace.review(args.candidate_id)
        digest = review["candidate_hash"]
        if args.digest is not None and digest != args.digest:
            raise ControlError("CANDIDATE_CHANGED")
        receipt = control.latest_pass(args.candidate_id, digest)
        if (
            receipt is None or receipt.get("status") != "PASSED"
            or receipt.get("candidate_hash") != digest or receipt.get("tests_run", 0) < 1
        ):
            raise ControlError("VALIDATION_PASS_REQUIRED")
        print(json.dumps({"review": review, "validation": receipt}, ensure_ascii=False, indent=2))
        exported = workspace.export(args.candidate_id, digest)
        if candidate_digest(exported) != digest:
            raise ControlError("CANDIDATE_CHANGED")
        identity = review["extension_id"]
        operation = ["upgrade", identity, str(exported)] if args.upgrade else [
            "install", str(exported)
        ]
        code = _host(python, admin_url, operation)
        if code != 0:
            return code
        if args.enable:
            return _host(python, admin_url, ["enable", identity])
        return 0
    except (ValueError, OSError, KeyError, TypeError, KeyboardInterrupt, EOFError) as exc:
        raw_code = getattr(exc, "code", None)
        error_code = raw_code if isinstance(raw_code, str) else "LOCAL_OPERATION_FAILED"
        print(json.dumps({"ok": False, "code": error_code}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
