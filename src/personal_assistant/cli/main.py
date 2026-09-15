from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

from personal_assistant.bootstrap import build_container
from personal_assistant.cli.scaffold import scaffold_extension
from personal_assistant.settings import Settings


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


def _doctor() -> int:
    try:
        settings = Settings.from_env()
        container = build_container(settings)
        manifests = container.extension_registry.discover(
            str(container.bundled_extensions_root)
        )
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
    for name in ("enable", "disable", "upgrade", "rollback", "uninstall", "purge"):
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
    if command == "list":
        method, path, payload = "GET", "/admin/v1/extensions", None
    elif command == "status":
        method, path, payload = "GET", f"/admin/v1/extensions/{args.extension_id}", None
    elif command == "inspect":
        method, path, payload = "POST", "/admin/v1/extensions/inspect", {"source": args.source}
    elif command == "install":
        method, path, payload = "POST", "/admin/v1/extensions/install", {"source": args.source}
    else:
        method = "POST"
        path = f"/admin/v1/extensions/{args.extension_id}/{command}"
        payload = {}
    try:
        status_code, result = _request(args.admin_url, method, path, payload)
    except OSError as exc:
        _print({"ok": False, "error": "ADMIN_API_UNREACHABLE", "message": str(exc)})
        return 2
    _print(result)
    return 0 if 200 <= status_code < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
