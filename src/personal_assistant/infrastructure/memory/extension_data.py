"""Fail-closed extension data capability for non-PostgreSQL storage backends."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from personal_assistant.core.extensions.data_access import (
    DataAccessError,
    ExtensionDataContext,
)


class UnavailableExtensionDataAccess:
    """Every request fails closed: dev memory storage has no extension database.

    Extensions must degrade safely (for example FTS-only or an explicit
    "unknown" answer) instead of silently pretending data was persisted.
    """

    @property
    def available(self) -> bool:
        return False

    async def handle(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        context: ExtensionDataContext,
    ) -> Any:
        del method, params, context
        raise DataAccessError(
            "DATA_UNAVAILABLE",
            "the extension data capability requires the PostgreSQL storage backend",
        )


__all__ = ["UnavailableExtensionDataAccess"]
