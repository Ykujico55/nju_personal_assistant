"""Connection pool, shared-transaction context and adapter lifecycle.

Repository adapters never hold a long-lived connection. When a repository method
runs inside :meth:`PostgresDatabase.transaction`, every nested call reuses the
same connection via a context variable so the whole unit of work commits or
rolls back together. Importing this module performs no I/O.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

import asyncpg

from personal_assistant.core.secrets import SecretHandle, SecretStorePort

from .config import PostgresAdapterConfig, normalize_dsn
from .migrate import run_migrations


async def _init_connection(connection: asyncpg.Connection) -> None:
    for type_name in ("json", "jsonb"):
        await connection.set_type_codec(
            type_name,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


class PostgresDatabase:
    """Owns the pool and the migration/readiness lifecycle."""

    def __init__(self, config: PostgresAdapterConfig) -> None:
        self._config = config
        self._dsn = normalize_dsn(config.database_url)
        self._pool: asyncpg.Pool | None = None
        self._current: ContextVar[asyncpg.Connection | None] = ContextVar(
            "pa_pg_connection", default=None
        )
        self._extra: list[asyncpg.Connection] = []
        self._secret: tuple[SecretHandle, SecretStorePort] | None = None

    @property
    def config(self) -> PostgresAdapterConfig:
        return self._config

    @property
    def started(self) -> bool:
        return self._pool is not None

    def use_secret_reference(self, handle: SecretHandle, store: SecretStorePort) -> None:
        if self.started or handle.kind != "database_dsn":
            raise ValueError("database credential reference cannot change after connection")
        self._secret = (handle, store)
        self._dsn = ""

    async def connect(self) -> None:
        """Connect without migrations, suitable for a read-only backup export."""
        if self._pool is None:
            if self._secret is not None:
                try:
                    handle, store = self._secret
                    dsn = normalize_dsn(
                        await store.resolve_for_broker(handle, purpose="database.connect")
                    )
                    if not dsn.startswith("postgresql://"):
                        raise ValueError()
                    self._dsn = dsn
                except Exception:
                    raise ValueError("database credential is unavailable") from None
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=self._config.pool_min_size,
                max_size=self._config.pool_max_size,
                command_timeout=self._config.command_timeout,
                init=_init_connection,
            )

    async def startup(self) -> tuple[str, ...]:
        """Connect, then validate/apply migrations. Raises on any failure."""
        await self.connect()
        return await run_migrations(self)

    async def close(self) -> None:
        for connection in self._extra:
            with contextlib.suppress(Exception):
                await connection.close()
        self._extra.clear()
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[asyncpg.Connection]:
        current = self._current.get()
        if current is not None:
            yield current
            return
        pool = self._require_pool()
        async with pool.acquire() as connection:
            yield connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        if self._current.get() is not None:
            yield
            return
        pool = self._require_pool()
        async with pool.acquire() as connection, connection.transaction():
            token = self._current.set(connection)
            try:
                yield
            finally:
                self._current.reset(token)

    async def new_extra_connection(self) -> asyncpg.Connection:
        """Create a dedicated connection (migrations, LISTEN) tracked for close."""

        connection = await asyncpg.connect(dsn=self._dsn)
        await _init_connection(connection)
        self._extra.append(connection)
        return connection

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("database is not started")
        return self._pool
