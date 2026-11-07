"""Private boot/PID readiness evidence; no process metadata enters public health."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime

from .control import HostControl

COMPONENTS = frozenset({"public", "health", "admin", "agent"})


def mark_ready(control: HostControl, component: str, boot_id: str) -> None:
    if component not in COMPONENTS or re.fullmatch(r"[0-9a-f]{32}", boot_id) is None:
        return
    control._write(
        "ready-" + component + ".json",
        {
            "boot_id": boot_id,
            "pid": os.getpid(),
            "parent_pid": os.getppid(),
            "seen_at": datetime.now(UTC).isoformat(),
        },
    )


def ready(control: HostControl, component: str, pid: int | None, boot_id: str) -> bool:
    if component not in COMPONENTS or type(pid) is not int:
        return False
    value = control.read("ready-" + component + ".json")
    if (
        set(value) != {"boot_id", "pid", "parent_pid", "seen_at"}
        or value.get("boot_id") != boot_id
        or type(value.get("pid")) is not int
        or type(value.get("parent_pid")) is not int
        or (value["pid"] != pid and value["parent_pid"] != pid)
    ):
        return False
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(value["seen_at"])).total_seconds()
        return age >= -5 and (component != "agent" or age <= 30)
    except (ValueError, TypeError):
        return False
