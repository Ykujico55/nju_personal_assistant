"""Fail-closed credential backend.

The Windows Credential Manager adapter is F09 work.  Until then the production
composition root binds remote model providers to this store, so a credential
resolution always fails with a typed error instead of silently inventing one or
falling back to an in-memory store.
"""

from __future__ import annotations

from personal_assistant.core.secrets import SecretHandle, SecretUnavailableError


class UnavailableSecretStore:
    """Implements ``SecretStorePort`` by refusing every operation."""

    async def put(self, *, name: str, kind: str, value: str) -> SecretHandle:
        del name, kind, value
        raise SecretUnavailableError(
            "host credential storage is not implemented in this build"
        )

    async def resolve_for_broker(self, handle: SecretHandle, *, purpose: str) -> str:
        del handle, purpose
        raise SecretUnavailableError(
            "host credential storage is not implemented in this build"
        )

    async def delete(self, handle: SecretHandle) -> None:
        del handle
        raise SecretUnavailableError(
            "host credential storage is not implemented in this build"
        )
