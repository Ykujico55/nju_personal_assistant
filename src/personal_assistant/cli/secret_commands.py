"""Local credential management; no secret values in arguments, files or responses."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import warnings
from typing import NoReturn

from personal_assistant.bootstrap import build_secret_store
from personal_assistant.core.secrets import SecretHandle


class SecretSafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message  # argparse's message can contain rejected secret arguments.
        self.print_usage(sys.stderr)
        self.exit(2, "assistantctl: invalid arguments; never pass secret values as arguments\n")


def add_secret_commands(commands: argparse._SubParsersAction[SecretSafeArgumentParser]) -> None:
    secret = commands.add_parser("secret", help="manage this Windows user's host credentials")
    actions = secret.add_subparsers(dest="secret_command", required=True)
    put = actions.add_parser("put", help="read a secret from a hidden interactive terminal")
    put.add_argument("--name", required=True, help="non-sensitive label")
    put.add_argument("--kind", required=True, help="host credential category")
    delete = actions.add_parser("delete", help="delete an opaque reference, idempotently")
    delete.add_argument("--id", required=True, help="opaque wcm1_ reference")
    delete.add_argument("--kind", required=True)


def run_secret_command(args: argparse.Namespace) -> int:
    result: dict[str, object] | None = None
    value: str | None = None
    try:
        store = build_secret_store()
        if args.secret_command == "put":
            if not sys.stdin.isatty() or not sys.stderr.isatty():
                raise ValueError("a hidden interactive terminal is required")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = getpass.getpass("Credential value (hidden): ")
            handle = asyncio.run(store.put(name=args.name, kind=args.kind, value=value))
            result = {"ok": True, "id": handle.id, "kind": handle.kind, "handle": str(handle)}
        else:
            handle = SecretHandle(id=args.id, kind=args.kind)
            asyncio.run(store.delete(handle))
            result = {"ok": True, "deleted": str(handle)}
    except (Exception, KeyboardInterrupt):
        # Never print exception text or prompt fallback warnings.
        result = None
    finally:
        del value
    if result is None:
        print(json.dumps({"ok": False, "error": "SECRET_UNAVAILABLE",
                          "message": "Credential operation failed; use a hidden local terminal."}))
        return 1
    print(json.dumps(result))
    return 0
