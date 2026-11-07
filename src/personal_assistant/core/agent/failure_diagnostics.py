"""Allowlisted Agent failure classifications; never persist exception text or attributes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from uuid import uuid4

from personal_assistant.core.extensions.config import ExtensionConfigError
from personal_assistant.core.extensions.errors import ExtensionOperationError
from personal_assistant.core.models.errors import (
    ModelCredentialUnavailableError,
    ModelOutputFilteredError,
    ModelOutputTruncatedError,
    ModelProviderProtocolError,
    ModelProviderRejectedError,
    ModelProviderTimeoutError,
    ModelProviderUnavailableError,
)
from personal_assistant.core.secrets.store import SecretUnavailableError
from personal_assistant.core.tools.gateway import ExtensionUnavailableError
from personal_assistant.domain import NotFoundError

_MESSAGES = {
    "MODEL_CREDENTIAL_UNAVAILABLE": "模型凭据不可用；请检查本机安全凭据配置。",
    "MODEL_PROVIDER_REJECTED": "模型服务拒绝了请求；请检查模型配置、权限或额度。",
    "MODEL_PROVIDER_TIMEOUT": "模型请求超时；原任务已保留，不会自动重试。",
    "MODEL_PROVIDER_UNAVAILABLE": "模型连接不可用；请检查网络与模型服务。",
    "MODEL_OUTPUT_TRUNCATED": "模型明确因输出限制停止；本次回复不完整。",
    "MODEL_OUTPUT_FILTERED": "模型明确过滤了本次回复；本次回复不完整。",
    "MODEL_PROVIDER_PROTOCOL_ERROR": "模型响应不符合协议；请检查模型接口兼容性。",
    "EXTENSION_CONFIGURATION_FAILED": "扩展配置未通过校验；请在扩展配置中检查。",
    "EXTENSION_UNAVAILABLE": "所需扩展或能力当前不可用；请检查启用及恢复状态。",
    "TOOL_UNAVAILABLE": "当前工具或版本不可用；这不代表目录未配置，请核对固定工具清单。",
    "HOST_RESOURCE_UNAVAILABLE": "所需宿主资源当前不可用；请检查资源与扩展状态。",
    "SECRET_UNAVAILABLE": "宿主安全凭据不可用；请检查本机凭据配置。",
    "TASK_DISPATCH_FAILED": "执行暂时失败；原检查点已保留，请凭关联编号检查。",
}


def failure_diagnostic(value: object) -> dict[str, str] | None:
    """Rebuild stored diagnostics from a fixed code and a host-generated opaque ID."""
    if not isinstance(value, Mapping):
        return None
    code, correlation = value.get("code"), value.get("correlation_id")
    if (
        not isinstance(code, str)
        or code not in _MESSAGES
        or not isinstance(correlation, str)
        or re.fullmatch(r"diag_[0-9a-f]{32}", correlation) is None
    ):
        return None
    return {"code": code, "message": _MESSAGES[code], "correlation_id": correlation}


def new_failure_diagnostic(error: Exception, *, tool_lookup: bool = False) -> dict[str, str]:
    code = "TASK_DISPATCH_FAILED"
    kinds = (
        (ModelCredentialUnavailableError, "MODEL_CREDENTIAL_UNAVAILABLE"),
        (ModelProviderRejectedError, "MODEL_PROVIDER_REJECTED"),
        (ModelProviderTimeoutError, "MODEL_PROVIDER_TIMEOUT"),
        (ModelProviderUnavailableError, "MODEL_PROVIDER_UNAVAILABLE"),
        (ModelOutputTruncatedError, "MODEL_OUTPUT_TRUNCATED"),
        (ModelOutputFilteredError, "MODEL_OUTPUT_FILTERED"),
        (ModelProviderProtocolError, "MODEL_PROVIDER_PROTOCOL_ERROR"),
        (ExtensionConfigError, "EXTENSION_CONFIGURATION_FAILED"),
        (ExtensionUnavailableError, "EXTENSION_UNAVAILABLE"),
        (SecretUnavailableError, "SECRET_UNAVAILABLE"),
    )
    for kind, candidate in kinds:
        if isinstance(error, kind):
            code = candidate
            break
    if (
        isinstance(error, ExtensionOperationError)
        and isinstance(error.code, str)
        and error.code
        in {
            "EXTENSION_NOT_FOUND",
            "EXTENSION_NOT_INSTALLED",
            "EXTENSION_NOT_ENABLED",
            "REQUIRED_CAPABILITY_UNAVAILABLE",
            "CAPABILITY_NOT_PUBLISHED",
            "INSTALL_PATH_MISSING",
            "RECOVERY_FAILED",
            "HEALTHCHECK_FAILED",
        }
    ):
        code = "EXTENSION_UNAVAILABLE"
    if isinstance(error, NotFoundError):
        code = "TOOL_UNAVAILABLE" if tool_lookup else "HOST_RESOURCE_UNAVAILABLE"
    return {"code": code, "message": _MESSAGES[code], "correlation_id": "diag_" + uuid4().hex}


def diagnostic_message(diagnostic: dict[str, str]) -> str:
    return (
        f"{diagnostic['message']} [{diagnostic['code']}; "
        f"关联编号 {diagnostic['correlation_id']}] 外部动作不会自动重试；未决时须先对账。"
    )
