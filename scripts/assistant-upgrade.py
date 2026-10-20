"""Offline upgrade: exact material consent, verified backup, frozen host, staged installation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from personal_assistant.infrastructure.operations.control import HostControl
from personal_assistant.infrastructure.operations.host import settings_from_profile
from personal_assistant.infrastructure.operations.upgrade import (
    activate_pointer,
    confirm_release,
    plan_release,
    select_python,
    stage_release,
)


def checked(argv: list[str]) -> None:
    result = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    if result.returncode:
        raise ValueError("candidate command failed; keep host frozen")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preview", "apply", "rollback-code"])
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--accept-hash")
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = settings_from_profile(args.profile, root=root)
    release_root = root / "var/releases"
    release_root.mkdir(parents=True, exist_ok=True)
    control = HostControl(settings.extension_root.parent / "control")
    try:
        if args.action == "rollback-code":
            if (
                not control.frozen()
                or not (control.root / "status.json").exists()
                or not json.loads((control.root / "status.json").read_text()).get("stopped")
            ):
                raise ValueError("freeze and stop the host before rollback")
            previous = json.loads((release_root / "previous.json").read_text())
            python = select_python(release_root, previous)
            value = activate_pointer(
                release_root,
                python=python,
                release_hash=previous["release_hash"],
                backup_id=previous["backup_id"],
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "code_pointer": value,
                        "database_restore_required": True,
                        "frozen": True,
                    }
                )
            )
            return 0
        if not args.wheelhouse:
            raise ValueError("an offline wheelhouse is required")
        plan = plan_release(list(args.wheelhouse.glob("*.whl")))
        if args.action == "preview":
            print(json.dumps(plan, indent=2))
            return 0
        confirm_release(plan, args.accept_hash or "")
        if (
            not control.frozen()
            or not (control.root / "status.json").exists()
            or not json.loads((control.root / "status.json").read_text()).get("stopped")
        ):
            raise ValueError("freeze and stop the host before upgrade")
        # The backup script verifies a complete encrypted restore point; no backup means no switch.
        output = subprocess.run(
            [
                sys.executable,
                "-Xutf8",
                str(root / "scripts/assistant-backup.py"),
                "create",
                "--profile",
                str(args.profile),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if output.returncode:
            raise ValueError("upgrade backup failed")
        receipt = json.loads(output.stdout)
        backup_id = receipt["backup"]["name"]
        destination = release_root / plan["hash"]
        if destination.exists():
            raise ValueError("candidate already exists; review or remove it explicitly")
        checked([sys.executable, "-m", "venv", str(destination)])
        staged = stage_release(plan, args.accept_hash or "", destination / "reviewed-wheels")
        python = destination / "Scripts/python.exe"
        checked(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                *[str(w) for w in staged],
            ]
        )
        checked([str(python), "-m", "pip", "check"])
        checked(
            [
                str(python),
                "-c",
                "from personal_assistant.app import create_app; "
                "from personal_assistant.health_app import create_app as health; "
                "assert health().openapi_url is None",
            ]
        )
        value = activate_pointer(
            release_root, python=python, release_hash=plan["hash"], backup_id=backup_id
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "candidate": value,
                    "frozen": True,
                    "requires_health_check_before_resume": True,
                }
            )
        )
        return 0
    except Exception:
        control.freeze()
        print('{"ok":false,"code":"UPGRADE_FAILED","frozen":true,"previous_version_retained":true}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
