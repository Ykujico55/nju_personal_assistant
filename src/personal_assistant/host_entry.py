"""Explicit local host entry; child commands receive profiles, never credential values."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from personal_assistant.infrastructure.operations.control import HostControl
from personal_assistant.infrastructure.operations.diagnostics import (
    HostDiagnosticError,
    classify,
    write_child_diagnostic,
)
from personal_assistant.infrastructure.operations.host import guard, restart, settings_from_profile
from personal_assistant.infrastructure.operations.readiness import mark_ready


def run_http_child(
    application: Any, port: int, component: str, control: HostControl, boot_id: str = ""
) -> None:
    """Keep lifespan failures visible even when the ASGI server consumes them."""
    import uvicorn

    original = application.router.lifespan_context
    failure: HostDiagnosticError | None = None

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        nonlocal failure
        try:
            async with original(app):
                yield
        except Exception as exc:
            failure = classify(exc, component=component, default="HOST_COMPONENT_START_FAILED")
            write_child_diagnostic(control, failure)
            raise failure from None

    application.router.lifespan_context = lifespan
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )

    async def run() -> None:
        async def observe() -> None:
            marked = False
            while not server.should_exit:
                if server.started and not marked:
                    mark_ready(control, component, boot_id)
                    marked = True
                if control.stopping(boot_id or None):
                    server.should_exit = True
                    return
                await asyncio.sleep(0.05)

        watcher = asyncio.create_task(observe())
        try:
            await server.serve()
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher

    try:
        asyncio.run(run())
    except SystemExit:
        if failure is None:
            failure = HostDiagnosticError("HOST_COMPONENT_START_FAILED", component=component)
    if failure is not None:
        raise failure


def main() -> int:
    parser = argparse.ArgumentParser(description="助手本机主机管理")
    parser.add_argument(
        "action",
        choices=[
            "serve",
            "restart",
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
    parser.add_argument("--admin", action="store_true", default=None)
    parser.add_argument("--boot-id", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    control: HostControl | None = None
    component = (
        args.action.removeprefix("child-") if args.action.startswith("child-") else "guardian"
    )
    try:
        settings = settings_from_profile(args.profile, root=root)
        if args.boot_id is not None:
            if re.fullmatch(r"[0-9a-f]{32}", args.boot_id) is None:
                raise HostDiagnosticError("HOST_PROFILE_INVALID")
            os.environ["PA_HOST_BOOT_ID"] = args.boot_id
        control = HostControl(settings.extension_root.parent / "control")
        if args.action == "serve":
            asyncio.run(guard(args.profile, root, admin=bool(args.admin), boot_id=args.boot_id))
        elif args.action == "restart":
            print(json.dumps(asyncio.run(restart(args.profile, root, admin=args.admin))))
        elif args.action.startswith("child-"):
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
                run_http_child(app, port, component, control, args.boot_id or "")
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
    except Exception as exc:
        diagnostic = classify(
            exc,
            component=component,
            default="HOST_COMPONENT_START_FAILED"
            if component != "guardian"
            else "HOST_OPERATION_FAILED",
        )
        if control is not None:
            with contextlib.suppress(OSError):
                write_child_diagnostic(control, diagnostic)
        print(json.dumps(diagnostic.response(), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
