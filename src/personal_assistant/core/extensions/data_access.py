"""Generic host-owned data capability for trusted extensions.

Extensions never receive database credentials, connection strings or host
paths.  They send parameterized statements over the full-duplex JSON-RPC channel
and the host executes them against that extension's own ``ext_*`` namespace.
This is a convenience boundary and an integrity guard for trusted extension
code, not a hostile-code sandbox.

The SQL guard is intentionally conservative: one statement at a time, a small
leading-keyword allowlist, no multi-statement scripts, no cross-schema or
catalog access, and bounded parameters.  Namespace scoping additionally sets
``search_path`` to the extension's schema for every statement.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .errors import ExtensionOperationError
from .models import data_namespace

HOST_DATA_EXECUTE = "host.data.execute"
HOST_DATA_TRANSACTION = "host.data.transaction"
HOST_DATA_MIGRATE = "host.data.migrate"

HOST_DATA_METHODS = frozenset(
    {HOST_DATA_EXECUTE, HOST_DATA_TRANSACTION, HOST_DATA_MIGRATE}
)

# Manifest capability that grants access to the generic extension data broker.
CAPABILITY_EXTENSION_DATA = "extension.data.sql"

MAX_STATEMENT_BYTES = 65_536
MAX_PARAMETERS = 256
MAX_PARAMETER_BYTES = 262_144
MAX_PARAMETER_DEPTH = 32
MAX_TRANSACTION_STATEMENTS = 512
MAX_MIGRATION_BYTES = 1_048_576

_DATA_ERROR_CODES = frozenset(
    {
        "DATA_UNAVAILABLE",
        "DATA_STATEMENT_REJECTED",
        "DATA_TIMEOUT",
        "DATA_MIGRATION_INVALID",
        "DATA_RESULT_TOO_LARGE",
        "DATA_PROTOCOL_ERROR",
        "DATA_INTERNAL_ERROR",
    }
)

_ALLOWED_LEADING_KEYWORDS = frozenset(
    {
        "SELECT",
        "WITH",
        "VALUES",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "ALTER",
        "DROP",
        "COMMENT",
        "TRUNCATE",
    }
)

_DENIED_SUBSTRINGS = (
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "dblink",
    "postgres_fdw",
    "set_config",
    "search_path",
    " set schema ",
    "create extension",
    "drop extension",
    "alter system",
    "set role",
    "set session authorization",
    "security definer",
    "pg_catalog",
    "information_schema",
    "pg_toast",
    "pg_temp",
    "pg_shadow",
    "pg_authid",
    "pg_subscription",
    "public.",
    "create schema",
    "drop schema",
    "create database",
    "drop database",
    "create role",
    "alter role",
    "drop role",
    "create user",
    "alter user",
    "drop user",
    "program ",
)

_FORBIDDEN_SCHEMA_RE = re.compile(
    r'"(?:public|information_schema|pg_catalog|pg_toast|pg_temp\w*)"\s*\.'
    r"|\b(?:public|information_schema|pg_catalog|pg_toast|pg_temp\w*)\s*\.",
    re.IGNORECASE,
)
_EXT_SCHEMA_RE = re.compile(r'\b(ext_[a-z0-9_]*)\s*\.', re.IGNORECASE)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_LEADING_KEYWORD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")


class DataAccessError(ExtensionOperationError):
    """Typed data-capability failure with an allowed diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        if code not in _DATA_ERROR_CODES:
            code = "DATA_INTERNAL_ERROR"
        super().__init__(code, message)


@dataclass(frozen=True, slots=True)
class ExtensionDataContext:
    """Identity and payload root bound to one worker process."""

    extension_id: str
    extension_version: str
    namespace: str
    payload_root: Path | None = None


@dataclass(frozen=True, slots=True)
class ExtensionMigration:
    version: int
    path: str
    checksum: str
    description: str = ""


@runtime_checkable
class ExtensionDataAccess(Protocol):
    @property
    def available(self) -> bool: ...

    async def handle(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        context: ExtensionDataContext,
    ) -> Any: ...


def namespace_for(extension_id: str) -> str:
    return data_namespace(extension_id)


def validate_statement(
    statement: str,
    *,
    namespace: str,
    forbidden_relations: Collection[str] = (),
) -> str:
    """Guard one SQL statement; return it with surrounding whitespace stripped.

    ``forbidden_relations`` lets the host deny unqualified references to core
    (non-extension) tables even when the pgvector type schema has to stay on the
    search path.
    """

    if not isinstance(statement, str) or not statement.strip():
        raise DataAccessError("DATA_STATEMENT_REJECTED", "statement must be a non-empty string")
    raw = statement.strip()
    if len(raw.encode("utf-8")) > MAX_STATEMENT_BYTES:
        raise DataAccessError("DATA_STATEMENT_REJECTED", "statement exceeds the size limit")
    stripped = _strip_single_statement(raw)
    lowered = stripped.lower()
    for token in _DENIED_SUBSTRINGS:
        if token in lowered:
            raise DataAccessError(
                "DATA_STATEMENT_REJECTED", f"statement uses a denied construct: {token}"
            )
    keyword_source = _strip_leading_comments(stripped)
    match = _LEADING_KEYWORD_RE.match(keyword_source)
    keyword = match.group(0).upper() if match else ""
    if keyword not in _ALLOWED_LEADING_KEYWORDS:
        raise DataAccessError(
            "DATA_STATEMENT_REJECTED", f"statement kind is not allowed: {keyword or 'unknown'}"
        )
    if _FORBIDDEN_SCHEMA_RE.search(stripped):
        raise DataAccessError(
            "DATA_STATEMENT_REJECTED", "statement references a schema outside the extension"
        )
    for found in _EXT_SCHEMA_RE.findall(stripped):
        if found.lower() != namespace.lower():
            raise DataAccessError(
                "DATA_STATEMENT_REJECTED", "statement references another extension namespace"
            )
    _reject_forbidden_relations(
        stripped, forbidden_relations, code="DATA_STATEMENT_REJECTED"
    )
    return stripped


def validate_migration_sql(
    sql: str,
    *,
    namespace: str,
    forbidden_relations: Collection[str] = (),
) -> str:
    """Guard a multi-statement migration file before host-side execution.

    Migration files are integrity-bound by a checksum declared in the installed,
    user-confirmed payload.  They are still constrained to the extension's own
    namespace and may not touch catalogs or other schemas.
    """

    if not isinstance(sql, str) or not sql.strip():
        raise DataAccessError("DATA_MIGRATION_INVALID", "migration file is empty")
    if len(sql.encode("utf-8")) > MAX_MIGRATION_BYTES:
        raise DataAccessError("DATA_MIGRATION_INVALID", "migration file exceeds the size limit")
    lowered = sql.lower()
    for token in _DENIED_SUBSTRINGS:
        if token in lowered:
            raise DataAccessError(
                "DATA_MIGRATION_INVALID", f"migration uses a denied construct: {token}"
            )
    if _FORBIDDEN_SCHEMA_RE.search(sql):
        raise DataAccessError(
            "DATA_MIGRATION_INVALID", "migration references a schema outside the extension"
        )
    for found in _EXT_SCHEMA_RE.findall(sql):
        if found.lower() != namespace.lower():
            raise DataAccessError(
                "DATA_MIGRATION_INVALID", "migration references another extension namespace"
            )
    _reject_forbidden_relations(
        sql, forbidden_relations, code="DATA_MIGRATION_INVALID"
    )
    statements = _split_migration_statements(sql)
    if not statements:
        raise DataAccessError("DATA_MIGRATION_INVALID", "migration file has no statements")
    for statement in statements:
        try:
            validate_statement(
                statement,
                namespace=namespace,
                forbidden_relations=forbidden_relations,
            )
        except DataAccessError as exc:
            raise DataAccessError(
                "DATA_MIGRATION_INVALID", "migration contains a rejected statement"
            ) from exc
    return sql


def validate_parameters(parameters: Sequence[Any]) -> tuple[Any, ...]:
    if len(parameters) > MAX_PARAMETERS:
        raise DataAccessError("DATA_STATEMENT_REJECTED", "too many statement parameters")
    validated: list[Any] = []
    for value in parameters:
        validated.append(_validate_parameter(value, depth=0))
    return tuple(validated)


def _validate_parameter(value: Any, *, depth: int) -> Any:
    if depth > MAX_PARAMETER_DEPTH:
        raise DataAccessError(
            "DATA_STATEMENT_REJECTED", "parameter nesting exceeds the depth limit"
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise DataAccessError(
            "DATA_STATEMENT_REJECTED", "numeric parameters must be finite"
        )
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value.encode("utf-8")) > MAX_PARAMETER_BYTES:
            raise DataAccessError("DATA_STATEMENT_REJECTED", "a parameter value is too large")
        return value
    if isinstance(value, (list, tuple)):
        return [_validate_parameter(item, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DataAccessError(
                    "DATA_STATEMENT_REJECTED", "parameter object keys must be strings"
                )
            result[key] = _validate_parameter(item, depth=depth + 1)
        return result
    raise DataAccessError(
        "DATA_STATEMENT_REJECTED", "parameter values must be JSON scalars, arrays or objects"
    )


def parse_migrations(value: Any) -> tuple[ExtensionMigration, ...]:
    if not isinstance(value, (list, tuple)):
        raise DataAccessError("DATA_MIGRATION_INVALID", "migrations must be a list")
    migrations: list[ExtensionMigration] = []
    versions: set[int] = set()
    paths: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise DataAccessError("DATA_MIGRATION_INVALID", "each migration must be an object")
        version = item.get("version")
        path = item.get("path")
        checksum = item.get("checksum")
        description = item.get("description", "")
        if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
            raise DataAccessError(
                "DATA_MIGRATION_INVALID", "migration version must be a positive integer"
            )
        if (
            not isinstance(path, str)
            or not path
            or path.startswith(("/", "\\"))
            or _WINDOWS_DRIVE_RE.match(path)
            or ".." in Path(path).parts
            or ":" in path
        ):
            raise DataAccessError(
                "DATA_MIGRATION_INVALID",
                "migration path must be a relative path inside the extension payload",
            )
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise DataAccessError(
                "DATA_MIGRATION_INVALID", "migration checksum must be a lowercase sha256 hex digest"
            )
        if not isinstance(description, str) or len(description) > 512:
            raise DataAccessError(
                "DATA_MIGRATION_INVALID", "migration description must be a short string"
            )
        if version in versions:
            raise DataAccessError("DATA_MIGRATION_INVALID", "migration versions must be unique")
        if path in paths:
            raise DataAccessError("DATA_MIGRATION_INVALID", "migration paths must be unique")
        versions.add(version)
        paths.add(path)
        migrations.append(
            ExtensionMigration(
                version=version,
                path=path,
                checksum=checksum,
                description=description,
            )
        )
    migrations.sort(key=lambda item: item.version)
    return tuple(migrations)


def resolve_migration_path(payload_root: Path, migration: ExtensionMigration) -> Path:
    root = Path(payload_root).resolve()
    candidate = (root / migration.path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise DataAccessError(
            "DATA_MIGRATION_INVALID", "migration path escapes the extension payload"
        ) from exc
    if not candidate.is_file():
        raise DataAccessError("DATA_MIGRATION_INVALID", "migration file is missing")
    return candidate


def verify_migration_checksum(path: Path, expected: str) -> bytes:
    """Read one bounded snapshot, verify it, and return those exact bytes."""

    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_MIGRATION_BYTES + 1)
    except OSError as exc:
        raise DataAccessError(
            "DATA_MIGRATION_INVALID", "migration file could not be read"
        ) from exc
    if len(data) > MAX_MIGRATION_BYTES:
        raise DataAccessError("DATA_MIGRATION_INVALID", "migration file exceeds the size limit")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise DataAccessError(
            "DATA_MIGRATION_INVALID", "migration checksum does not match the descriptor"
        )
    return data


def validate_transaction_items(value: Any) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise DataAccessError("DATA_STATEMENT_REJECTED", "statements must be a non-empty list")
    if len(value) > MAX_TRANSACTION_STATEMENTS:
        raise DataAccessError("DATA_STATEMENT_REJECTED", "too many statements in one transaction")
    items: list[tuple[str, tuple[Any, ...]]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise DataAccessError(
                "DATA_STATEMENT_REJECTED", "each transaction item must be an object"
            )
        statement = item.get("statement")
        parameters = item.get("parameters", [])
        if not isinstance(parameters, (list, tuple)):
            raise DataAccessError(
                "DATA_STATEMENT_REJECTED", "statement parameters must be a list"
            )
        items.append((str(statement), validate_parameters(parameters)))
    return tuple(items)


def _reject_forbidden_relations(
    sql: str, forbidden_relations: Collection[str], *, code: str
) -> None:
    if not forbidden_relations:
        return
    forbidden = {name.lower() for name in forbidden_relations}
    words = {word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql)}
    denied = sorted(words & forbidden)
    if denied:
        raise DataAccessError(
            code, f"statement references a core relation: {denied[0]}"
        )


def _strip_single_statement(statement: str) -> str:
    body = statement.rstrip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    if not body:
        raise DataAccessError("DATA_STATEMENT_REJECTED", "statement is empty")
    _scan_single_statement(body)
    return body


def _scan_single_statement(statement: str) -> None:
    index = 0
    length = len(statement)
    while index < length:
        character = statement[index]
        if character == "'":
            index = _skip_quoted(statement, index, "'")
            continue
        if character == '"':
            index = _skip_quoted(statement, index, '"')
            continue
        if character == "$":
            tag_match = re.match(r"\$[A-Za-z_0-9]*\$", statement[index:])
            if tag_match is not None:
                tag = tag_match.group(0)
                end = statement.find(tag, index + len(tag))
                if end == -1:
                    raise DataAccessError(
                        "DATA_STATEMENT_REJECTED", "unterminated dollar-quoted string"
                    )
                index = end + len(tag)
                continue
            index += 1
            continue
        if character == "-" and statement.startswith("--", index):
            newline = statement.find("\n", index)
            index = length if newline == -1 else newline + 1
            continue
        if character == "/" and statement.startswith("/*", index):
            end = statement.find("*/", index + 2)
            if end == -1:
                raise DataAccessError("DATA_STATEMENT_REJECTED", "unterminated block comment")
            index = end + 2
            continue
        if character == ";":
            raise DataAccessError(
                "DATA_STATEMENT_REJECTED", "only one statement may be executed at a time"
            )
        index += 1


def _split_migration_statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    start = 0
    index = 0
    length = len(sql)
    while index < length:
        character = sql[index]
        if character == "'":
            index = _skip_quoted(sql, index, "'")
            continue
        if character == '"':
            index = _skip_quoted(sql, index, '"')
            continue
        if character == "$":
            tag_match = re.match(r"\$[A-Za-z_0-9]*\$", sql[index:])
            if tag_match is not None:
                tag = tag_match.group(0)
                end = sql.find(tag, index + len(tag))
                if end == -1:
                    raise DataAccessError(
                        "DATA_MIGRATION_INVALID", "unterminated dollar-quoted string"
                    )
                index = end + len(tag)
                continue
        if character == "-" and sql.startswith("--", index):
            newline = sql.find("\n", index)
            index = length if newline == -1 else newline + 1
            continue
        if character == "/" and sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end == -1:
                raise DataAccessError(
                    "DATA_MIGRATION_INVALID", "unterminated block comment"
                )
            index = end + 2
            continue
        if character == ";":
            candidate = sql[start:index].strip()
            if _strip_leading_comments(candidate):
                statements.append(candidate)
            start = index + 1
        index += 1
    candidate = sql[start:].strip()
    if _strip_leading_comments(candidate):
        statements.append(candidate)
    return tuple(statements)


def _strip_leading_comments(statement: str) -> str:
    remaining = statement.lstrip()
    while remaining:
        if remaining.startswith("--"):
            newline = remaining.find("\n")
            if newline == -1:
                return ""
            remaining = remaining[newline + 1 :].lstrip()
            continue
        if remaining.startswith("/*"):
            end = remaining.find("*/", 2)
            if end == -1:
                return ""
            remaining = remaining[end + 2 :].lstrip()
            continue
        break
    return remaining


def _skip_quoted(statement: str, start: int, quote: str) -> int:
    index = start + 1
    length = len(statement)
    while index < length:
        character = statement[index]
        if character == quote:
            if index + 1 < length and statement[index + 1] == quote:
                index += 2
                continue
            return index + 1
        if character == "\\" and index + 1 < length:
            index += 2
            continue
        index += 1
    raise DataAccessError("DATA_STATEMENT_REJECTED", "unterminated quoted string")


__all__ = [
    "CAPABILITY_EXTENSION_DATA",
    "HOST_DATA_EXECUTE",
    "HOST_DATA_MIGRATE",
    "HOST_DATA_METHODS",
    "HOST_DATA_TRANSACTION",
    "DataAccessError",
    "ExtensionDataAccess",
    "ExtensionDataContext",
    "ExtensionMigration",
    "namespace_for",
    "parse_migrations",
    "resolve_migration_path",
    "validate_migration_sql",
    "validate_parameters",
    "validate_statement",
    "validate_transaction_items",
    "verify_migration_checksum",
]
