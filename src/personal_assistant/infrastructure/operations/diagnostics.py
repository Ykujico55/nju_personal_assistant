"""Fixed startup diagnostics; exception text and credential values never persist."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg

from personal_assistant.core.extensions.errors import ExtensionError
from personal_assistant.core.models.errors import ModelCredentialUnavailableError
from personal_assistant.settings import ConfigurationError

from .control import HostControl

MESSAGES = {
    "HOST_RESTART_IN_PROGRESS": "此实例已有重启操作；未启动另一套后台。",
    "HOST_RESTART_UNSUPPORTED": "旧守护器缺少可靠启动身份；请先正常 stop，再 start 加载新版。",
    "HOST_NOT_RUNNING": "此实例没有运行中的守护器；请使用 start。",
    "HOST_STOP_TIMEOUT": "原守护器尚未正常退出；没有启动第二套后台，保持冻结。",
    "HOST_READY_TIMEOUT": "新版组件未全部就绪；保持冻结并请求停止本次启动。",
    "HOST_ALREADY_RUNNING": "此实例已由另一个守护进程运行；未修改原服务。",
    "HOST_PROFILE_INVALID": "主机配置无效；请核对配置格式和非秘密字段。",
    "HOST_PORT_IN_USE": "所需本机端口已被占用；请检查已有服务。",
    "HOST_DATABASE_CREDENTIAL_UNAVAILABLE": "数据库凭据句柄不可用；请重新配置本机凭据。",
    "HOST_MODEL_CREDENTIAL_UNAVAILABLE": "模型凭据句柄不可用；请重新配置本机凭据。",
    "HOST_DATABASE_UNAVAILABLE": "数据库连接或启动校验失败；请检查数据库状态。",
    "HOST_RECOVERY_FAILED": "扩展恢复失败；请按关联编号检查安全诊断。",
    "HOST_COMPONENT_START_FAILED": "子组件启动失败；请按关联编号检查配置。",
    "HOST_COMPONENT_EXITED": "子组件意外退出；已保留关联编号。",
    "HOST_OPERATION_FAILED": "主机操作失败；请按关联编号检查本机状态。",
}
COMPONENTS = frozenset({"guardian", "public", "health", "agent", "admin"})


class HostDiagnosticError(ValueError):
    def __init__(self, code: str, *, component: str = "guardian", diagnostic_id: str | None = None):
        self.code = code if code in MESSAGES else "HOST_OPERATION_FAILED"
        self.component = component if component in COMPONENTS else "guardian"
        self.diagnostic_id = (
            diagnostic_id
            if isinstance(diagnostic_id, str) and re.fullmatch(r"diag_[0-9a-f]{32}", diagnostic_id)
            else "diag_" + uuid4().hex
        )
        super().__init__(MESSAGES[self.code])

    def metadata(self) -> dict[str, Any]:
        return {"code": self.code, "diagnostic_id": self.diagnostic_id, "component": self.component}

    def response(self) -> dict[str, Any]:
        return {"ok": False, **self.metadata(), "message": MESSAGES[self.code]}


def classify(
    error: BaseException, *, component: str = "guardian", default: str = "HOST_OPERATION_FAILED"
) -> HostDiagnosticError:
    if isinstance(error, HostDiagnosticError):
        return error
    if isinstance(error, ModelCredentialUnavailableError):
        code = "HOST_MODEL_CREDENTIAL_UNAVAILABLE"
    elif isinstance(error, ConfigurationError):
        code = "HOST_PROFILE_INVALID"
    elif isinstance(error, (asyncpg.PostgresError, ConnectionError, TimeoutError)):
        code = "HOST_DATABASE_UNAVAILABLE"
    elif isinstance(error, ExtensionError):
        code = "HOST_RECOVERY_FAILED"
    else:
        code = default
    return HostDiagnosticError(code, component=component)


def write_child_diagnostic(control: HostControl, diagnostic: HostDiagnosticError) -> None:
    if diagnostic.component == "guardian":
        return
    control._write(
        "diagnostic-" + diagnostic.component + ".json",
        {**diagnostic.metadata(), "pid": os.getpid(), "parent_pid": os.getppid()},
    )


def read_child_diagnostic(
    root: Path, component: str, pid: int | None
) -> HostDiagnosticError | None:
    if component not in COMPONENTS or component == "guardian" or type(pid) is not int:
        return None
    try:
        with (root / ("diagnostic-" + component + ".json")).open("rb") as stream:
            raw = stream.read(2049)
        if len(raw) > 2048:
            return None
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value)
            not in (
                {"code", "diagnostic_id", "component", "pid"},
                {"code", "diagnostic_id", "component", "pid", "parent_pid"},
            )
            or value.get("component") != component
            or type(value.get("pid")) is not int
            or ("parent_pid" in value and type(value["parent_pid"]) is not int)
            or (value["pid"] != pid and value.get("parent_pid") != pid)
            or value.get("code") not in MESSAGES
            or not isinstance(value.get("diagnostic_id"), str)
            or re.fullmatch(r"diag_[0-9a-f]{32}", value["diagnostic_id"]) is None
        ):
            return None
        return HostDiagnosticError(
            value["code"], component=component, diagnostic_id=value["diagnostic_id"]
        )
    except (OSError, ValueError, TypeError):
        return None
