"""Local workbench token totals use the existing append-only model audit."""
from __future__ import annotations

from typing import Any

from fastapi import Depends

from personal_assistant.api.dependencies import get_container
from personal_assistant.api.errors import ApplicationError
from personal_assistant.bootstrap import Container
from personal_assistant.core.audit.usage import ModelUsageReaderPort


async def usage(container: Container = Depends(get_container)) -> dict[str, Any]:
    if not isinstance(container.audit_writer, ModelUsageReaderPort):
        raise ApplicationError("USAGE_UNAVAILABLE", "当前存储未提供模型用量统计。", 503)
    return await container.audit_writer.model_usage()
