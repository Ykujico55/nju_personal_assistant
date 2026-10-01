"""Read-only catalog of enabled extension FormSchemaProvider descriptors."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from personal_assistant.api.dependencies import get_container
from personal_assistant.bootstrap import Container

router = APIRouter(prefix="/forms", tags=["forms"])


@router.get("")
async def list_forms(
    container: Container = Depends(get_container),
) -> dict[str, list[dict[str, Any]]]:
    return {"items": list(await container.extension_supervisor.list_forms())}
