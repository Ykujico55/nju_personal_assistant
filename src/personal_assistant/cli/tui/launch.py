"""Optional UI entry point: connects to Admin, never starts/unfreezes the host."""

from __future__ import annotations

import sys
from pathlib import Path

from .model_settings import ModelSettings
from .service import WorkbenchError, WorkbenchService, loopback_url


def run(
    *,
    admin_url: str,
    profile: Path | None = None,
    model_config: Path = Path("var/config/model.json"),
    refresh: int = 5,
) -> int:
    try:
        address = loopback_url(admin_url)
    except WorkbenchError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    try:
        from .app import WorkbenchApp
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] in {"textual", "rich"}:
            print(
                'TUI 依赖未安装。请在助手环境运行：python -m pip install -e ".[tui]"',
                file=sys.stderr,
            )
            return 2
        raise
    settings = (
        ModelSettings(profile, profile=True, defaults_path=model_config)
        if profile is not None
        else ModelSettings(model_config)
    )
    service = WorkbenchService(address, settings_store=settings)
    WorkbenchApp(service, refresh_seconds=refresh).run()
    return 0
