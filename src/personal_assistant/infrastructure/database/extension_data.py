"""PostgreSQL adapter for the generic extension data capability.

Every statement runs inside a transaction whose ``search_path`` is set to the
calling extension's own ``ext_*`` namespace, with a bounded statement timeout.
Migrations declared by the extension's ``MigrationProvider`` are applied from the
installed payload with a verified SHA-256 checksum and recorded in a ledger
inside that same namespace; no core table stores extension business data.
"""

from __future__ import annotations

import base64
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

from personal_assistant.core.extensions.data_access import (
    HOST_DATA_EXECUTE,
    HOST_DATA_METHODS,
    HOST_DATA_TRANSACTION,
    DataAccessError,
    ExtensionDataContext,
    parse_migrations,
    resolve_migration_path,
    validate_migration_sql,
    validate_parameters,
    validate_statement,
    validate_transaction_items,
    verify_migration_checksum,
)

from .connection import PostgresDatabase

VECTOR_TYPE_SCHEMA_SQL = (
    "SELECT n.nspname FROM pg_type t "
    "JOIN pg_namespace n ON n.oid = t.typnamespace "
    "WHERE t.typname = 'vector' ORDER BY n.nspname LIMIT 1"
)
CORE_RELATIONS_SQL = (
    "SELECT c.relname FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') "
    "AND n.nspname NOT IN ('information_schema') "
    "AND n.nspname NOT LIKE 'pg\\_%' "
    "AND n.nspname NOT LIKE 'ext\\_%' "
    "AND n.nspname <> $1"
)

DEFAULT_EXECUTE_TIMEOUT_SECONDS = 30.0
DEFAULT_TRANSACTION_TIMEOUT_SECONDS = 120.0
DEFAULT_MIGRATION_TIMEOUT_SECONDS = 300.0
MAX_TIMEOUT_SECONDS = 900.0
MAX_RESULT_ROWS = 10_000
MAX_RESULT_BYTES = 512 * 1024

_LEDGER_TABLE = "extension_data_migrations"
_NAMESPACE_RE = re.compile(r"^[a-z0-9_]{1,63}$")


class PostgresExtensionDataAccess:
    """Implements ``ExtensionDataAccess`` over the host connection pool."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._type_schemas: tuple[str, ...] | None = None

    @property
    def available(self) -> bool:
        return True

    async def handle(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        context: ExtensionDataContext,
    ) -> Any:
        _require_namespace(context.namespace)
        if method not in HOST_DATA_METHODS:
            raise DataAccessError("DATA_PROTOCOL_ERROR", f"unknown host data method: {method}")
        try:
            return await self._dispatch(method, params, context=context)
        except asyncpg.exceptions.QueryCanceledError as exc:
            # Namespace locks, search-path setup and migration-ledger work are
            # part of the same deadline as the user statement.  Never leak a
            # raw driver exception when one of those setup steps times out.
            raise DataAccessError(
                "DATA_TIMEOUT", "extension data operation exceeded its deadline"
            ) from exc

    async def _dispatch(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        context: ExtensionDataContext,
    ) -> Any:
        forbidden = await self._forbidden_relations(context.namespace)
        if method == HOST_DATA_EXECUTE:
            statement = validate_statement(
                params.get("statement", ""),
                namespace=context.namespace,
                forbidden_relations=forbidden,
            )
            parameters = validate_parameters(params.get("parameters", []))
            timeout = _timeout(
                params.get("timeout_seconds"), DEFAULT_EXECUTE_TIMEOUT_SECONDS
            )
            return await self._execute(context, statement, parameters, timeout)
        if method == HOST_DATA_TRANSACTION:
            items = validate_transaction_items(params.get("statements", []))
            timeout = _timeout(
                params.get("timeout_seconds"), DEFAULT_TRANSACTION_TIMEOUT_SECONDS
            )
            return await self._transaction(context, items, timeout, forbidden)
        migrations = parse_migrations(params.get("migrations", []))
        timeout = _timeout(
            params.get("timeout_seconds"), DEFAULT_MIGRATION_TIMEOUT_SECONDS
        )
        return await self._migrate(context, migrations, timeout, forbidden)

    async def _execute(
        self,
        context: ExtensionDataContext,
        statement: str,
        parameters: Sequence[Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        async with self._database.connection() as connection, connection.transaction():
            await self._ensure_namespace(connection, context.namespace, timeout_seconds)
            await self._scope_transaction(connection, context.namespace, timeout_seconds)
            return await _run_statement(connection, statement, parameters)

    async def _transaction(
        self,
        context: ExtensionDataContext,
        items: Sequence[tuple[str, tuple[Any, ...]]],
        timeout_seconds: float,
        forbidden_relations: frozenset[str],
    ) -> Mapping[str, Any]:
        results: list[Mapping[str, Any]] = []
        result_bytes = 2
        async with self._database.connection() as connection, connection.transaction():
            await self._ensure_namespace(connection, context.namespace, timeout_seconds)
            await self._scope_transaction(connection, context.namespace, timeout_seconds)
            for statement, parameters in items:
                guarded = validate_statement(
                    statement,
                    namespace=context.namespace,
                    forbidden_relations=forbidden_relations,
                )
                result = await _run_statement(connection, guarded, parameters)
                result_bytes += _json_size(result) + 1
                if result_bytes > MAX_RESULT_BYTES:
                    raise DataAccessError(
                        "DATA_RESULT_TOO_LARGE",
                        "transaction results exceed the response size limit",
                    )
                results.append(result)
        return {"results": results}

    async def _migrate(
        self,
        context: ExtensionDataContext,
        migrations: Sequence[Any],
        timeout_seconds: float,
        forbidden_relations: frozenset[str],
    ) -> Mapping[str, Any]:
        payload_root = context.payload_root
        if payload_root is None:
            raise DataAccessError(
                "DATA_MIGRATION_INVALID", "the extension payload root is unavailable"
            )
        namespace = context.namespace
        applied: list[int] = []
        skipped: list[int] = []
        async with self._database.connection() as connection, connection.transaction():
            await self._ensure_namespace(connection, namespace, timeout_seconds)
            await self._scope_transaction(connection, namespace, timeout_seconds)
            # Serialize migrations for one extension namespace across processes.
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
                "pa_extension_data_migrations",
                namespace,
            )
            await connection.execute(
                f'CREATE TABLE IF NOT EXISTS "{namespace}"."{_LEDGER_TABLE}" ('
                "version integer PRIMARY KEY, "
                "checksum char(64) NOT NULL, "
                "description text NOT NULL DEFAULT '', "
                "applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for migration in migrations:
                recorded = await connection.fetchval(
                    f'SELECT checksum FROM "{namespace}"."{_LEDGER_TABLE}" '
                    "WHERE version = $1",
                    migration.version,
                )
                if recorded is not None:
                    if recorded != migration.checksum:
                        raise DataAccessError(
                            "DATA_MIGRATION_INVALID",
                            f"recorded migration {migration.version} has a different checksum",
                        )
                    skipped.append(migration.version)
                    continue
                path = resolve_migration_path(Path(payload_root), migration)
                verified_bytes = verify_migration_checksum(path, migration.checksum)
                sql = validate_migration_sql(
                    _decode_migration(verified_bytes),
                    namespace=namespace,
                    forbidden_relations=forbidden_relations,
                )
                try:
                    await connection.execute(sql)
                except asyncpg.exceptions.QueryCanceledError as exc:
                    raise DataAccessError(
                        "DATA_TIMEOUT", "migration exceeded the extension data deadline"
                    ) from exc
                await connection.execute(
                    f'INSERT INTO "{namespace}"."{_LEDGER_TABLE}" '
                    "(version, checksum, description) VALUES ($1, $2, $3)",
                    migration.version,
                    migration.checksum,
                    migration.description,
                )
                applied.append(migration.version)
        return {
            "namespace": namespace,
            "applied": applied,
            "skipped": skipped,
        }

    async def _ensure_namespace(
        self,
        connection: asyncpg.Connection,
        namespace: str,
        timeout_seconds: float,
    ) -> None:
        """Create the bound namespace before it becomes the first search path entry.

        PostgreSQL silently skips non-existent schemas in ``search_path``.  If
        the pgvector type lives in ``public``, an extension's first unqualified
        DDL would otherwise land in ``public`` before its migration runs.
        """

        await connection.execute(
            "SELECT set_config('statement_timeout', $1, true)",
            str(int(timeout_seconds * 1000)),
        )
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
            "pa_extension_data_namespace",
            namespace,
        )
        await connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{namespace}"')

    async def _scope_transaction(
        self, connection: asyncpg.Connection, namespace: str, timeout_seconds: float
    ) -> None:
        schemas = await self._vector_schemas(connection)
        parts = [f'"{namespace}"']
        parts.extend(f'"{schema}"' for schema in schemas)
        parts.append("pg_catalog")
        await connection.execute(
            "SELECT set_config('search_path', $1, true)", ", ".join(parts)
        )
        await connection.execute(
            "SELECT set_config('statement_timeout', $1, true)",
            str(int(timeout_seconds * 1000)),
        )

    async def _vector_schemas(
        self, connection: asyncpg.Connection
    ) -> tuple[str, ...]:
        """Schema that owns the pgvector ``vector`` type, if it is installed.

        Extension schemas need the type on the search path; core table access is
        separately denied through the statement guard's forbidden relations.
        """

        if self._type_schemas is None:
            # Reuse the already-acquired transaction connection.  Acquiring a
            # second connection here deadlocks a valid pool with max_size=1 and
            # can exhaust larger pools when every caller is in this setup step.
            schema = await connection.fetchval(VECTOR_TYPE_SCHEMA_SQL)
            self._type_schemas = (schema,) if isinstance(schema, str) and schema else ()
        return self._type_schemas

    async def _forbidden_relations(self, namespace: str) -> frozenset[str]:
        # Core/public relations can be created after startup.  A cached list
        # would let a newly-created core table become reachable through the
        # pgvector schema on the extension search path.
        async with self._database.connection() as connection:
            rows = await connection.fetch(CORE_RELATIONS_SQL, namespace)
        return frozenset(str(row[0]) for row in rows)


def _decode_migration(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DataAccessError(
            "DATA_MIGRATION_INVALID", "migration files must be UTF-8 encoded"
        ) from exc


async def _run_statement(
    connection: asyncpg.Connection, statement: str, parameters: Sequence[Any]
) -> Mapping[str, Any]:
    try:
        if _returns_rows(statement):
            records = await connection.fetch(statement, *parameters)
            if len(records) > MAX_RESULT_ROWS:
                raise DataAccessError("DATA_RESULT_TOO_LARGE", "statement returned too many rows")
            rows: list[dict[str, Any]] = []
            result_bytes = 2
            for record in records:
                row = _row_to_json(record)
                result_bytes += _json_size(row) + 1
                if result_bytes > MAX_RESULT_BYTES:
                    raise DataAccessError(
                        "DATA_RESULT_TOO_LARGE",
                        "statement result exceeds the response size limit",
                    )
                rows.append(row)
            return {"rows": rows, "rowcount": len(rows)}
        status = await connection.execute(statement, *parameters)
    except asyncpg.exceptions.QueryCanceledError as exc:
        raise DataAccessError(
            "DATA_TIMEOUT", "statement exceeded the extension data deadline"
        ) from exc
    except asyncpg.PostgresError as exc:
        raise DataAccessError(
            "DATA_STATEMENT_REJECTED", f"statement failed: {type(exc).__name__}"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise DataAccessError("DATA_STATEMENT_REJECTED", "statement is not executable") from exc
    return {"rows": [], "rowcount": _status_count(status)}


def _returns_rows(statement: str) -> bool:
    leading = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
    if leading in {"SELECT", "VALUES", "WITH"}:
        return True
    return re.search(r"\bRETURNING\b", statement, re.IGNORECASE) is not None


def _status_count(status: str) -> int:
    parts = status.split()
    if parts and parts[-1].isdigit():
        return int(parts[-1])
    return 0


def _row_to_json(record: asyncpg.Record) -> dict[str, Any]:
    return {key: _jsonify(value) for key, value in dict(record).items()}


def _jsonify(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DataAccessError(
                "DATA_PROTOCOL_ERROR", "statement result contains a non-finite number"
            )
        return value
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DataAccessError(
                "DATA_PROTOCOL_ERROR", "statement result contains a non-finite number"
            )
        number = float(value)
        if not math.isfinite(number):
            raise DataAccessError(
                "DATA_PROTOCOL_ERROR", "statement result number exceeds JSON range"
            )
        return number
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, Mapping):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(item) for item in value]
    try:
        encoded = json.dumps(value, default=str, allow_nan=False)
        return json.loads(encoded)
    except (TypeError, ValueError):
        return str(value)


def _json_size(value: Any) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise DataAccessError(
            "DATA_PROTOCOL_ERROR", "statement result is not strict JSON"
        ) from exc


def _timeout(value: Any, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataAccessError("DATA_PROTOCOL_ERROR", "timeout_seconds must be a number")
    if not math.isfinite(float(value)) or value <= 0 or value > MAX_TIMEOUT_SECONDS:
        raise DataAccessError("DATA_PROTOCOL_ERROR", "timeout_seconds is out of range")
    return float(value)


def _require_namespace(namespace: str) -> None:
    if not isinstance(namespace, str) or _NAMESPACE_RE.fullmatch(namespace) is None:
        raise DataAccessError("DATA_PROTOCOL_ERROR", "invalid extension data namespace")


__all__ = [
    "DEFAULT_EXECUTE_TIMEOUT_SECONDS",
    "DEFAULT_MIGRATION_TIMEOUT_SECONDS",
    "DEFAULT_TRANSACTION_TIMEOUT_SECONDS",
    "MAX_RESULT_BYTES",
    "PostgresExtensionDataAccess",
]
