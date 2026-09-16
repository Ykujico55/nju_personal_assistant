"""Extension data retention adapters.

Uninstall keeps business data by default.  Only the extension's own ``ext_*``
schema can be named here, and permanent purge stays explicitly unimplemented
until an independent impact preview is part of the product flow.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from personal_assistant.core.extensions.errors import ExtensionOperationError
from personal_assistant.core.extensions.models import data_namespace

if TYPE_CHECKING:
    from personal_assistant.infrastructure.database.connection import PostgresDatabase


class PostgresExtensionDataStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def namespaces(self, extension_id: str) -> Sequence[str]:
        schema = data_namespace(extension_id)
        async with self._database.connection() as connection:
            rows = await connection.fetch(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = $1",
                schema,
            )
        return tuple(row["schema_name"] for row in rows)

    async def purge(self, extension_id: str) -> None:
        raise ExtensionOperationError(
            "PURGE_NOT_IMPLEMENTED",
            f"permanent data purge requires an independent confirmation flow: {extension_id}",
        )


class InMemoryExtensionDataStore:
    def __init__(self) -> None:
        self._namespaces: dict[str, set[str]] = {}

    def register(self, extension_id: str, namespace: str) -> None:
        self._namespaces.setdefault(extension_id, set()).add(namespace)

    async def namespaces(self, extension_id: str) -> Sequence[str]:
        return tuple(sorted(self._namespaces.get(extension_id, set())))

    async def purge(self, extension_id: str) -> None:
        raise ExtensionOperationError(
            "PURGE_NOT_IMPLEMENTED",
            f"permanent data purge requires an independent confirmation flow: {extension_id}",
        )


__all__ = ["InMemoryExtensionDataStore", "PostgresExtensionDataStore"]
