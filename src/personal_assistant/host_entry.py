"""Explicit local host entry; child commands receive profiles, never credential values."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from personal_assistant.infrastructure.operations.control import HostControl
from personal_assistant.infrastructure.operations.host import guard, settings_from_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="助手本机主机管理")
    parser.add_argument(
        "action",
        choices=[
            "serve",
            "stop",
            "status",
            "freeze",
            "resume",
            "child-public",
            "child-health",
            "child-agent",
            "child-admin",
        ],
    )
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--admin", action="store_true")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    try:
        settings = settings_from_profile(args.profile, root=root)
        control = HostControl(settings.extension_root.parent / "control")
        if args.action == "serve":
            asyncio.run(guard(args.profile, root, admin=args.admin))
        elif args.action.startswith("child-"):
            import uvicorn

            if args.action == "child-agent":
                from personal_assistant.workers.agent_worker import serve

                asyncio.run(serve())
            else:
                if args.action == "child-public":
                    from personal_assistant.app import create_app as create_public_app

                    app, port = create_public_app(settings=settings), settings.public_port
                elif args.action == "child-health":
                    from personal_assistant.health_app import create_app as create_health_app

                    app, port = create_health_app(), settings.health_port
                else:
                    from personal_assistant.admin_app import create_app as create_admin_app

                    app, port = create_admin_app(settings=settings), settings.admin_port
                uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
        elif args.action == "stop":
            control.freeze()
            control.request_stop()
        elif args.action == "freeze":
            control.freeze()
        elif args.action == "resume":
            control.resume()
        else:
            path = control.root / "status.json"
            print(path.read_text() if path.exists() else json.dumps({"running": False}))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "HOST_OPERATION_FAILED",
                    "message": "检查本机配置、端口和凭据句柄；保持外部动作冻结。",
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
