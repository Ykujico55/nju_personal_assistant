"""Run locally in an interactive terminal. Never supply an API key in arguments."""

from __future__ import annotations

import asyncio
import getpass
import json
import sys
import warnings
from pathlib import Path

from personal_assistant.bootstrap import build_secret_store
from personal_assistant.infrastructure.models.setup import configure, load_config, trial


def main() -> int:
    path = Path(__file__).resolve().parents[1] / "var/config/model.json"
    try:
        store = build_secret_store()
        if path.exists():
            settings = load_config(path)
        else:
            if not sys.stdin.isatty() or not sys.stderr.isatty():
                raise ValueError("interactive terminal required")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = getpass.getpass("DeepSeek API Key (hidden): ")
            try:
                settings = asyncio.run(configure(path, store, value))
            finally:
                del value
        print("Configured: deepseek-flash; credential retained by Windows Credential Manager.")
        print(json.dumps(asyncio.run(trial(settings, store)), ensure_ascii=False))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        code = getattr(error, "code", type(error).__name__)
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": code,
                    "message": "Setup or trial failed; settings retained. No automatic retry.",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
