"""Authenticated encrypted recovery packages; restore always freezes execution."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from personal_assistant.core.secrets import SecretHandle, SecretStorePort

MAGIC = b"PA-RECOVERY-1\x00"
MAX_BYTES = 256 * 1024 * 1024
TABLES = (
    "tasks",
    "agent_runs",
    "run_checkpoints",
    "approvals",
    "jobs",
    "tool_invocations",
    "audit_events",
    "extensions",
    "extension_versions",
    "extension_operations",
    "artifacts",
    "context_documents",
    "task_command_idempotency",
    "task_messages",
    "task_events",
    "run_observations",
    "side_effect_intents",
    "mail_delivery_actions",
    "browser_adapters",
    "task_form_drafts",
    "task_form_draft_commands",
    "runtime_records",
    "runtime_commands",
)
SECRET_KEYS = {
    "password",
    "api_key",
    "authorization",
    "access_token",
    "refresh_token",
    "cookie",
    "private_key",
    "token",
}
BLOB = re.compile(r"sha256/([0-9a-f]{2})/([0-9a-f]{64})")
EXT_TABLE = re.compile(r"ext_[a-z0-9_]{1,100}\.[a-z_][a-z0-9_]{0,62}")


def no_secrets(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("recovery data is too deep")
    if isinstance(value, dict):
        if any(str(k).lower() in SECRET_KEYS for k in value):
            raise ValueError("secret fields are excluded from recovery")
        if value.get("sensitivity") == "SECRET" or value.get("classification") == "SECRET":
            raise ValueError("SECRET data is excluded from recovery")
        for child in value.values():
            no_secrets(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            no_secrets(child, depth + 1)


def validate_snapshot(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or (type(value.get("format")) is not int or value.get("format") != 1)
        or not isinstance(value.get("tables"), dict)
        or not isinstance(value.get("artifacts"), dict)
    ):
        raise ValueError("unsupported recovery format")
    if any(t not in TABLES and not EXT_TABLE.fullmatch(t) for t in value["tables"]):
        raise ValueError("unmanaged or secret tables are excluded")
    if any(
        not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
        for rows in value["tables"].values()
    ):
        raise ValueError("invalid table snapshot")
    no_secrets(value)


class RecoveryVault:
    def __init__(self, store: SecretStorePort, handle: SecretHandle):
        if handle.kind != "backup_key":
            raise ValueError("backup requires a dedicated key handle")
        self.store, self.handle = store, handle

    async def _key(self) -> bytes:
        try:
            value = await self.store.resolve_for_broker(self.handle, purpose="backup.encrypt")
            key = bytes.fromhex(value)
            if len(key) != 32:
                raise ValueError()
            return key
        except Exception:
            raise ValueError("backup key is unavailable") from None

    async def seal(self, snapshot: dict[str, Any]) -> bytes:
        validate_snapshot(snapshot)
        raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError("recovery package exceeds configured limit")
        nonce = os.urandom(12)
        return MAGIC + nonce + AESGCM(await self._key()).encrypt(nonce, raw, MAGIC)

    async def open(self, data: bytes) -> dict[str, Any]:
        if (
            not data.startswith(MAGIC)
            or len(data) > MAX_BYTES + 1024
            or len(data) < len(MAGIC) + 28
        ):
            raise ValueError("invalid encrypted package")
        start = len(MAGIC)
        try:
            raw = AESGCM(await self._key()).decrypt(
                data[start : start + 12], data[start + 12 :], MAGIC
            )
            value = json.loads(raw)
            validate_snapshot(value)
            return cast(dict[str, Any], value)
        except Exception:
            raise ValueError("recovery authentication or validation failed") from None


def freeze_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    validate_snapshot(snapshot)
    result = copy.deepcopy(snapshot)
    tables = result["tables"]
    for row in tables.get("tasks", []):
        if row.get("status") not in {"SUCCEEDED", "FAILED", "CANCELLED", "WAITING_RECONCILIATION"}:
            row["status"] = "PAUSED_SAFETY"
            row["version"] = row.get("version", 0) + 1
    for row in tables.get("agent_runs", []):
        if row.get("state") not in {"SUCCEEDED", "FAILED", "CANCELLED", "WAITING_RECONCILIATION"}:
            row["state"] = "PAUSED_SAFETY"
            row["pause_reason"] = "RESTORE_REVIEW_REQUIRED"
            row["waiting_reference"] = None
            row["version"] = row.get("version", 0) + 1
    for row in tables.get("jobs", []):
        if row.get("state") not in {"SUCCEEDED", "FAILED", "DEAD_LETTER"}:
            row.update(
                state="DEAD_LETTER",
                lease_owner=None,
                lease_until=None,
                error_code="RESTORE_REVIEW_REQUIRED",
            )
    for row in tables.get("approvals", []):
        if row.get("status") == "EXECUTING":
            # The authorization was already consumed. Restore cannot infer the
            # external result or erase who approved it; keep those audit facts
            # while burning the old nonce and requiring read-only reconciliation.
            row.update(status="UNKNOWN", nonce=uuid4().hex)
            row["version"] = row.get("version", 0) + 1
        elif row.get("status") not in {"SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"}:
            row.update(status="EXPIRED", nonce=uuid4().hex, approved_by=None, approved_at=None)
            row["version"] = row.get("version", 0) + 1
    for table, column in [
        ("side_effect_intents", "state"),
        ("mail_delivery_actions", "status"),
        ("tool_invocations", "status"),
    ]:
        for row in tables.get(table, []):
            if row.get(column) in {"PREPARED", "EXECUTING", "RUNNING"}:
                row[column] = "UNKNOWN"
    for row in tables.get("extensions", []):
        row["lifecycle_state"] = "DISABLED"
    for row in tables.get("runtime_records", []):
        if row.get("bucket") in {"schedules", "event-bindings"}:
            row["value"]["enabled"] = False
        if row.get("bucket") == "requests":
            row["value"]["consent_id"] = None
        if row.get("bucket") == "executions" and row["value"].get("state") not in {
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
            "WAITING_RECONCILIATION",
        }:
            row["value"].update(
                state="PAUSED_SAFETY", reference=None, reason="RESTORE_REVIEW_REQUIRED"
            )
    tables["runtime_records"] = [
        r for r in tables.get("runtime_records", []) if r.get("bucket") != "hosts"
    ]
    result["credential_rebind_required"] = True
    return result


def stage_artifacts(artifacts: dict[str, Any], target: Path) -> None:
    # Validate every byte and path before creating the new, isolated staging directory.
    prepared: dict[str, bytes] = {}
    for name, item in artifacts.items():
        match = BLOB.fullmatch(name)
        if not match or match[1] != match[2][:2] or item.get("sha256") != match[2]:
            raise ValueError("unsafe artifact path")
        try:
            raw = base64.b64decode(item["data"], validate=True)
        except Exception:
            raise ValueError("invalid artifact bytes") from None
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != match[2]:
            raise ValueError("artifact hash mismatch")
        prepared[name] = raw
    if target.exists():
        raise FileExistsError("restore staging must be a new directory")
    target.mkdir(parents=True)
    for name, raw in prepared.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def retained_points(points: dict[str, datetime]) -> frozenset[str]:
    ordered = sorted(points.items(), key=lambda p: p[1], reverse=True)
    keep: set[str] = set()
    periods: list[tuple[int, Callable[[datetime], object]]] = [
        (7, lambda d: d.date()),
        (4, lambda d: d.isocalendar()[:2]),
        (6, lambda d: (d.year, d.month)),
    ]
    for limit, period in periods:
        seen: set[object] = set()
        for identifier, timestamp in ordered:
            if timestamp.tzinfo is None:
                raise ValueError("backup timestamps must be timezone aware")
            key = period(timestamp)
            if key not in seen:
                seen.add(key)
                keep.add(identifier)
                if len(seen) >= limit:
                    break
    return frozenset(keep)


async def export_database(database: Any, artifact_root: Path) -> dict[str, Any]:
    """One REPEATABLE READ snapshot. Does not run migrations or import extension code."""
    tables: dict[str, Any] = {}
    artifacts: dict[str, Any] = {}
    async with (
        database.connection() as connection,
        connection.transaction(isolation="repeatable_read", readonly=True),
    ):
        names = await connection.fetch(
            "SELECT table_schema,table_name FROM information_schema.tables "
            "WHERE table_type='BASE TABLE' AND "
            "(table_schema='public' OR table_schema LIKE 'ext_%') "
            "ORDER BY table_schema,table_name"
        )
        for item in names:
            schema, name = item["table_schema"], item["table_name"]
            identifier = name if schema == "public" else schema + "." + name
            if identifier not in TABLES and not EXT_TABLE.fullmatch(identifier):
                continue
            rows = [
                r["row"]
                for r in await connection.fetch(
                    f'SELECT to_jsonb(t) AS row FROM "{schema}"."{name}" t'
                )
            ]
            if identifier in {"artifacts", "context_documents"}:
                rows = [r for r in rows if r.get("sensitivity") != "SECRET"]
            tables[identifier] = rows
        migrations = [
            dict(r)
            for r in await connection.fetch(
                "SELECT version,checksum FROM schema_migrations ORDER BY version"
            )
        ]
        for row in tables.get("artifacts", []):
            if row.get("deleted_at"):
                continue
            key = row["storage_key"]
            match = BLOB.fullmatch(key)
            path = (artifact_root / key).resolve()
            if not match or artifact_root.resolve() not in path.parents or path.is_symlink():
                raise ValueError("artifact path is not managed")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise ValueError("artifact hash changed")
            artifacts[key] = {"sha256": row["sha256"], "data": base64.b64encode(raw).decode()}
    snapshot = {
        "format": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "tables": tables,
        "artifacts": artifacts,
        "migrations": migrations,
        "credential_rebind_required": True,
    }
    validate_snapshot(snapshot)
    return snapshot


async def preflight_restore_target(source_dsn: str, database: Any) -> None:
    """Require a new database name and empty targets before any migration writes."""
    from urllib.parse import unquote, urlsplit

    original_name = unquote(urlsplit(source_dsn).path).lstrip("/")
    if not original_name:
        raise ValueError("original database identity is required")
    async with database.connection() as connection:
        name = await connection.fetchval("SELECT current_database()")
        if name == original_name or not name.endswith(("_restore", "_test")):
            raise ValueError("restore requires a separate dedicated database name")
        tables = await connection.fetch(
            "SELECT table_schema,table_name FROM information_schema.tables "
            "WHERE table_type='BASE TABLE' AND "
            "table_schema NOT IN ('information_schema','pg_catalog')"
        )
        for row in tables:
            schema, table = row["table_schema"], row["table_name"]
            if schema == "public" and table == "schema_migrations":
                continue
            schema = schema.replace('"', '""')
            table = table.replace('"', '""')
            if await connection.fetchval(f'SELECT count(*) FROM "{schema}"."{table}"'):
                raise ValueError("restore refuses existing data before migrations")


async def restore_database(snapshot: dict[str, Any], database: Any) -> None:
    """Import into empty, migrated tables. Package SQL/code is never executed."""
    value = freeze_snapshot(snapshot)
    async with database.connection() as connection, connection.transaction():
        migrations = [
            dict(r)
            for r in await connection.fetch(
                "SELECT version,checksum FROM schema_migrations ORDER BY version"
            )
        ]
        if value.get("migrations") and [
            (r["version"], r["checksum"]) for r in value["migrations"]
        ] != [(r["version"], r["checksum"]) for r in migrations]:
            raise ValueError("restore schema version/checksum mismatch")
        pending = [t for t in TABLES if t in value["tables"]] + sorted(
            t for t in value["tables"] if EXT_TABLE.fullmatch(t)
        )
        dependencies: dict[str, set[str]] = {t: set() for t in pending}
        relations = await connection.fetch(
            "SELECT ns.nspname AS s,s.relname AS n,nt.nspname AS ps,t.relname AS pn "
            "FROM pg_constraint c JOIN pg_class s ON s.oid=c.conrelid "
            "JOIN pg_namespace ns ON ns.oid=s.relnamespace "
            "JOIN pg_class t ON t.oid=c.confrelid "
            "JOIN pg_namespace nt ON nt.oid=t.relnamespace WHERE c.contype='f'"
        )
        for relation in relations:
            child = (
                relation["n"] if relation["s"] == "public" else relation["s"] + "." + relation["n"]
            )
            parent = (
                relation["pn"]
                if relation["ps"] == "public"
                else relation["ps"] + "." + relation["pn"]
            )
            if child in dependencies and parent in dependencies and child != parent:
                dependencies[child].add(parent)
        ordered: list[str] = []
        while pending:
            ready = [t for t in pending if not dependencies[t].intersection(pending)]
            if not ready:
                raise ValueError("cyclic restore dependencies require explicit review")
            ordered.extend(ready)
            pending = [t for t in pending if t not in ready]
        for identifier in ordered:
            schema, name = identifier.split(".") if "." in identifier else ("public", identifier)
            if not await connection.fetchval("SELECT to_regclass($1)", schema + "." + name):
                raise ValueError(
                    "extension schema requires approved installation before data restore"
                )
            if await connection.fetchval(f'SELECT count(*) FROM "{schema}"."{name}"'):
                raise ValueError("restore refuses nonempty tables")
        for identifier in ordered:
            schema, name = identifier.split(".") if "." in identifier else ("public", identifier)
            columns = [
                r["column_name"]
                for r in await connection.fetch(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=$1 AND table_name=$2 AND is_generated='NEVER' "
                    "ORDER BY ordinal_position",
                    schema,
                    name,
                )
            ]
            rendered = ",".join('"' + c.replace('"', '""') + '"' for c in columns)
            for row in value["tables"][identifier]:
                if set(row) - set(columns) - {"search_vector"}:
                    raise ValueError("snapshot contains unknown columns")
                query = (
                    f'INSERT INTO "{schema}"."{name}" ({rendered}) OVERRIDING SYSTEM VALUE '
                    f"SELECT {rendered} FROM jsonb_populate_record("
                    f'NULL::"{schema}"."{name}",$1::jsonb)'
                )
                await connection.execute(query, {k: v for k, v in row.items() if k in columns})
            for column in columns:
                sequence = await connection.fetchval(
                    "SELECT pg_get_serial_sequence($1,$2)", schema + "." + name, column
                )
                if sequence:
                    maximum = await connection.fetchval(
                        f'SELECT max("{column}") FROM "{schema}"."{name}"'
                    )
                    await connection.execute(
                        "SELECT setval($1::regclass,$2,$3)",
                        sequence,
                        max(maximum or 1, 1),
                        maximum is not None,
                    )
        maximum = await connection.fetchval("SELECT max(sequence) FROM run_checkpoints")
        await connection.execute(
            "SELECT setval('run_checkpoint_sequence',$1,$2)",
            max(maximum or 1, 1),
            maximum is not None,
        )
