"""Deterministic, checksum-verified SQL migration runner.

The runner never connects at import time. It applies numbered ``*.sql`` files in
order under a PostgreSQL advisory lock so that concurrent processes starting at
the same time cannot race. Each migration and its version record are committed
in a single transaction; a failed migration leaves no ``schema_migrations`` row.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .connection import PostgresDatabase

MIGRATION_ADVISORY_LOCK_KEY = 743_192_235_100_001
_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    checksum char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    code = "MIGRATION_FAILED"


class MigrationChecksumError(MigrationError):
    code = "MIGRATION_CHECKSUM_MISMATCH"


def _iter_migrations(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise MigrationError(f"migrations directory is missing: {directory}")
    files = sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    if not files:
        raise MigrationError(f"no numbered migrations found in {directory}")
    return files


def _checksum(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strip_transaction_wrapper(sql: str) -> str:
    """Remove top-level BEGIN/COMMIT so the runner owns the transaction.

    The files themselves are immutable history; this only changes how they are
    executed so the version record can be committed atomically with the schema.
    """

    kept: list[str] = []
    for line in sql.splitlines():
        if line.strip().upper() in {"BEGIN;", "COMMIT;", "ROLLBACK;"}:
            continue
        kept.append(line)
    return "\n".join(kept)


async def run_migrations(database: PostgresDatabase) -> tuple[str, ...]:
    """Apply pending migrations and return the versions applied by this call."""

    directory = database.config.resolved_migrations_dir()
    files = _iter_migrations(directory)
    connection = await database.new_extra_connection()
    applied: dict[str, str] = {}
    newly_applied: list[str] = []
    try:
        await connection.execute("SELECT pg_advisory_lock($1)", MIGRATION_ADVISORY_LOCK_KEY)
        try:
            await connection.execute(_MIGRATIONS_TABLE)
            rows = await connection.fetch("SELECT version, checksum FROM schema_migrations")
            applied = {row["version"]: row["checksum"] for row in rows}
            for path in files:
                version = path.stem
                raw = path.read_bytes()
                checksum = _checksum(raw)
                recorded = applied.get(version)
                if recorded is not None:
                    if recorded != checksum:
                        raise MigrationChecksumError(
                            f"applied migration {version} checksum drifted; refusing to start"
                        )
                    continue
                body = _strip_transaction_wrapper(raw.decode("utf-8"))
                async with connection.transaction():
                    await connection.execute(body)
                    await connection.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                        version,
                        checksum,
                    )
                newly_applied.append(version)
            return tuple(newly_applied)
        finally:
            await connection.execute(
                "SELECT pg_advisory_unlock($1)", MIGRATION_ADVISORY_LOCK_KEY
            )
    finally:
        await connection.close()
