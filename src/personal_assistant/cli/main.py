"""Local control CLI.

Management commands never touch the database: they call the loopback Local Admin
API and poll operation ids so the CLI and the management UI share one state
machine.  Installation and upgrade require an explicit confirmation that is bound
to the exact preview (artifact hash, extension id, version and manifest).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

from personal_assistant.cli.scaffold import scaffold_extension
from personal_assistant.core.extensions import ExtensionRegistry
from personal_assistant.settings import Settings

_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED"}


def _request(
    admin_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if method != "GET":
        headers["Idempotency-Key"] = f"cli-{uuid4().hex}"
    request = urllib.request.Request(
        admin_url.rstrip("/") + path,
        method=method,
        data=data,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"error": {"code": "HTTP_ERROR", "message": body}}
        return exc.code, parsed


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _poll_operation(
    admin_url: str,
    operation_id: str,
    *,
    timeout_seconds: float = 600.0,
    interval_seconds: float = 0.5,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, body = _request(
            admin_url, "GET", f"/admin/v1/extension-operations/{operation_id}"
        )
        if status != 200:
            _print(body)
            return 1
        last = body
        if body.get("status") in _TERMINAL_STATUSES:
            _print(body)
            return 0 if body["status"] == "SUCCEEDED" else 1
        time.sleep(interval_seconds)
    _print(
        {
            "ok": False,
            "error": "OPERATION_TIMEOUT",
            "message": f"operation did not finish within {timeout_seconds:g} seconds",
            "last": last,
        }
    )
    return 1


def _bundled_extensions_root() -> Path:
    return Path(__file__).resolve().parents[3] / "extensions"


def _doctor() -> int:
    try:
        settings = Settings.from_env()
        manifests = ExtensionRegistry().discover(str(_bundled_extensions_root()))
    except Exception as exc:  # boundary prints a concise diagnostic
        _print({"ok": False, "error": type(exc).__name__, "message": str(exc)})
        return 1
    _print(
        {
            "ok": True,
            "environment": settings.environment,
            "storage": settings.storage_backend,
            "admin": f"http://{settings.admin_host}:{settings.admin_port}",
            "health": f"http://{settings.health_host}:{settings.health_port}/healthz",
            "discovered_extensions": [manifest.id for manifest in manifests],
        }
    )
    return 0


def _confirm(prompt: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        _print({"ok": False, "error": "CONFIRMATION_REQUIRED", "message": prompt})
        return False
    if answer in {"y", "yes"}:
        return True
    _print({"ok": False, "error": "CONFIRMATION_DECLINED", "message": "nothing was executed"})
    return False


def _reject_plan(admin_url: str, plan_id: str) -> None:
    try:
        status, body = _request(
            admin_url, "POST", f"/admin/v1/extension-plans/{plan_id}/reject"
        )
    except OSError as exc:
        _print({"ok": False, "error": "ADMIN_API_UNREACHABLE", "message": str(exc)})
        return
    if status >= 400:
        _print(body)


def _install_or_upgrade(
    admin_url: str, source: str, *, assume_yes: bool, upgrade_id: str | None
) -> int:
    status, preview = _request(
        admin_url, "POST", "/admin/v1/extensions/inspect", {"source": source}
    )
    if status != 200:
        _print(preview)
        return 1
    _print(preview)
    mode = preview.get("mode")
    if upgrade_id is not None:
        if mode != "upgrade" or preview.get("extension_id") != upgrade_id:
            _print({"ok": False, "error": "PLAN_MODE_MISMATCH", "message": str(preview)})
            return 1
        endpoint = f"/admin/v1/extensions/{upgrade_id}/upgrade"
        action = "Upgrade"
    else:
        if mode != "install":
            _print(
                {
                    "ok": False,
                    "error": "PLAN_MODE_MISMATCH",
                    "message": "use 'assistantctl extension upgrade <extension_id> <source>'",
                }
            )
            return 1
        endpoint = "/admin/v1/extensions/install"
        action = "Install"
    prompt = (
        f"{action} {preview.get('extension_id')} {preview.get('extension_version')} "
        f"({preview.get('artifact_hash')})? Installing means you trust this code; "
        "venv and worker isolate dependencies and crashes, not malicious code."
    )
    if not _confirm(prompt, assume_yes=assume_yes):
        # Clean up the staged plan so the extension id is not blocked.
        _reject_plan(admin_url, str(preview.get("plan_id")))
        return 1
    payload = {
        "plan_id": preview.get("plan_id"),
        "confirmation_nonce": preview.get("confirmation_nonce"),
        "preview_hash": preview.get("preview_hash"),
        "accepted_warning": True,
    }
    status, body = _request(admin_url, "POST", endpoint, payload)
    if status not in (200, 202):
        _print(body)
        return 1
    return _poll_operation(admin_url, str(body.get("id")))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assistantctl")
    parser.add_argument("--admin-url", default="http://127.0.0.1:8001")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    extension = commands.add_parser("extension")
    actions = extension.add_subparsers(dest="extension_command", required=True)
    actions.add_parser("list")
    status = actions.add_parser("status")
    status.add_argument("extension_id")
    inspect = actions.add_parser("inspect")
    inspect.add_argument("source")
    scaffold = actions.add_parser("scaffold")
    scaffold.add_argument("extension_id")
    scaffold.add_argument("destination")
    install = actions.add_parser("install")
    install.add_argument("source")
    install.add_argument("--yes", action="store_true", help="explicit non-interactive confirmation")
    upgrade = actions.add_parser("upgrade")
    upgrade.add_argument("extension_id")
    upgrade.add_argument("source")
    upgrade.add_argument("--yes", action="store_true", help="explicit non-interactive confirmation")
    for name in ("enable", "disable", "rollback", "uninstall", "purge"):
        operation = actions.add_parser(name)
        operation.add_argument("extension_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    command = args.extension_command
    if command == "scaffold":
        try:
            files = scaffold_extension(args.extension_id, Path(args.destination))
        except (ValueError, OSError) as exc:
            _print({"ok": False, "error": type(exc).__name__, "message": str(exc)})
            return 1
        _print({"ok": True, "created": [str(path) for path in files]})
        return 0
    if command == "install":
        return _install_or_upgrade(
            args.admin_url, args.source, assume_yes=args.yes, upgrade_id=None
        )
    if command == "upgrade":
        return _install_or_upgrade(
            args.admin_url, args.source, assume_yes=args.yes, upgrade_id=args.extension_id
        )
    if command == "list":
        method, path, payload = "GET", "/admin/v1/extensions", None
    elif command == "status":
        method, path, payload = "GET", f"/admin/v1/extensions/{args.extension_id}", None
    elif command == "inspect":
        method, path, payload = "POST", "/admin/v1/extensions/inspect", {"source": args.source}
    else:
        suffix = "purge-data" if command == "purge" else command
        method = "POST"
        path = f"/admin/v1/extensions/{args.extension_id}/{suffix}"
        payload = {}
    try:
        status_code, result = _request(args.admin_url, method, path, payload)
    except OSError as exc:
        _print({"ok": False, "error": "ADMIN_API_UNREACHABLE", "message": str(exc)})
        return 2
    if command in {"enable", "disable", "rollback", "uninstall"} and status_code in (200, 202):
        return _poll_operation(args.admin_url, str(result.get("id")))
    _print(result)
    return 0 if 200 <= status_code < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
