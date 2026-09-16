"""Configuration for the production PostgreSQL adapter set."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _resolve_migrations_dir() -> Path:
    source_tree = Path(__file__).resolve().parents[4] / "migrations"
    if source_tree.is_dir():
        return source_tree
    import sysconfig

    installed = Path(sysconfig.get_path("data")) / "share" / "personal-assistant" / "migrations"
    if installed.is_dir():
        return installed
    raise RuntimeError("database migrations are missing from this installation")


def normalize_dsn(database_url: str) -> str:
    """Translate a SQLAlchemy-style async URL into an asyncpg DSN.

    The credential portion is only ever handed to asyncpg; it is never logged.
    """

    dsn = database_url.strip()
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    if dsn.startswith("postgres://"):
        return "postgresql://" + dsn[len("postgres://") :]
    return dsn


@dataclass(frozen=True, slots=True)
class PostgresAdapterConfig:
    database_url: str
    owner_id: str = "owner"
    pool_min_size: int = 1
    pool_max_size: int = 10
    command_timeout: float = 60.0
    migrations_dir: Path | None = field(default=None)

    def resolved_migrations_dir(self) -> Path:
        return self.migrations_dir or _resolve_migrations_dir()

    @classmethod
    def from_env(cls, database_url: str) -> PostgresAdapterConfig:
        return cls(
            database_url=database_url,
            owner_id=os.getenv("PA_OWNER_ID", "owner").strip() or "owner",
        )
