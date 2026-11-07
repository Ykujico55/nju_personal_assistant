"""Local user's validation grants; candidate-edit tools cannot reach this store."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from .validation import candidate_digest, candidate_files

_TERMINAL = frozenset({"PASSED", "FAILED", "TIMED_OUT", "BLOCKED", "CANCELLED", "UNKNOWN"})
_RESULT_FIELDS = frozenset({
    "status", "candidate_hash", "tests_run", "failures", "errors", "skipped", "exit_code",
    "duration_seconds", "failed_tests", "log_name", "stdout_truncated", "stderr_truncated",
    "code",
})


class ControlError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class WorkspaceAccess(Protocol):
    @property
    def root(self) -> Path: ...

    def candidate_path(self, candidate_id: str) -> Path: ...


def _metadata_digest(path: Path) -> str:
    files = {item["path"]: item["sha256"] for item in candidate_files(path)}
    names = ("extension.toml", "requirements.lock", "pyproject.toml")
    if not all(name in files for name in names):
        raise ControlError("VALIDATION_METADATA_REQUIRED")
    data = json.dumps({name: files[name] for name in names}, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


def _bound_result(result: Mapping[str, Any], digest: str) -> dict[str, Any]:
    value = {key: item for key, item in result.items() if key in _RESULT_FIELDS}
    status = value.get("status")
    if status not in _TERMINAL or value.get("candidate_hash") != digest:
        raise ControlError("VALIDATION_RECEIPT_INVALID")
    if status == "PASSED":
        total, skipped = value.get("tests_run"), value.get("skipped", 0)
        if (
            type(total) is not int or total <= 0 or type(skipped) is not int or skipped < 0
            or total <= skipped or value.get("failures", 0) != 0
            or value.get("errors", 0) != 0 or value.get("exit_code") != 0
        ):
            raise ControlError("VALIDATION_RECEIPT_INVALID")
    log_name = value.get("log_name")
    if log_name is not None and (
        not isinstance(log_name, str) or Path(log_name).name != log_name
        or any(char in log_name for char in "/\\:")
    ):
        raise ControlError("VALIDATION_RECEIPT_INVALID")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ControlError("VALIDATION_RECEIPT_INVALID") from None
    if len(encoded.encode("utf-8")) > 65536:
        raise ControlError("VALIDATION_RECEIPT_INVALID")
    return value


class ControlStore:
    """SQLite grants and execution receipts outside every editable candidate."""

    def __init__(self, workspace: WorkspaceAccess) -> None:
        self.workspace = workspace
        self.root = workspace.root / "control"
        self.root.mkdir(exist_ok=True)
        self.database = self.root / "control.sqlite3"
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS validation_grants (
                    candidate_id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                    metadata_digest TEXT NOT NULL, expires_at REAL NOT NULL,
                    allow_edits INTEGER NOT NULL, max_runs INTEGER NOT NULL,
                    used_runs INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS validation_runs (
                    request_key TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
                    digest TEXT NOT NULL, token TEXT NOT NULL, status TEXT NOT NULL,
                    result_json TEXT, created_at REAL NOT NULL
                );
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        for path in (self.root, self.database):
            if path.exists():
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or bool(
                    getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise ControlError("CONTROL_PATH_INVALID")
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _candidate(self, candidate_id: str, digest: str) -> Path:
        path = self.workspace.candidate_path(candidate_id)
        if candidate_digest(path) != digest:
            raise ControlError("CANDIDATE_CHANGED")
        return path

    @staticmethod
    def _unconfirmed(connection: sqlite3.Connection, candidate_id: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM validation_runs WHERE candidate_id=? "
            "AND status IN ('RUNNING','UNKNOWN') LIMIT 1", (candidate_id,)
        ).fetchone() is not None

    def grant(
        self, candidate_id: str, digest: str, *, ttl_seconds: int = 900,
        allow_edits: bool = False, max_runs: int = 1,
    ) -> dict[str, Any]:
        if (
            type(allow_edits) is not bool or type(ttl_seconds) is not int
            or type(max_runs) is not int or ttl_seconds < 1 or max_runs < 1
            or ttl_seconds > (1800 if allow_edits else 900)
            or max_runs > (5 if allow_edits else 1)
        ):
            raise ControlError("VALIDATION_GRANT_INVALID")
        path = self._candidate(candidate_id, digest)
        metadata = _metadata_digest(path)
        expires = time.time() + ttl_seconds
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._unconfirmed(connection, candidate_id):
                raise ControlError("VALIDATION_UNCONFIRMED")
            connection.execute(
                "INSERT INTO validation_grants "
                "(candidate_id,digest,metadata_digest,expires_at,allow_edits,max_runs,used_runs) "
                "VALUES(?,?,?,?,?,?,0) ON CONFLICT(candidate_id) DO UPDATE SET "
                "digest=excluded.digest,metadata_digest=excluded.metadata_digest,"
                "expires_at=excluded.expires_at,allow_edits=excluded.allow_edits,"
                "max_runs=excluded.max_runs,used_runs=0",
                (candidate_id, digest, metadata, expires, int(allow_edits), max_runs),
            )
        return {"candidate_id": candidate_id, "candidate_hash": digest,
                "expires_at": expires, "allow_edits": allow_edits, "max_runs": max_runs}

    def authorize_run(self, candidate_id: str, digest: str, key: str) -> dict[str, Any]:
        if not isinstance(key, str) or not key or len(key) > 256:
            raise ControlError("VALIDATION_KEY_INVALID")
        path = self._candidate(candidate_id, digest)
        metadata = _metadata_digest(path)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT * FROM validation_runs WHERE request_key=?", (key,)
            ).fetchone()
            if previous:
                if previous["candidate_id"] != candidate_id or previous["digest"] != digest:
                    raise ControlError("VALIDATION_KEY_CONFLICT")
                if previous["status"] in {"RUNNING", "UNKNOWN"}:
                    raise ControlError("VALIDATION_UNCONFIRMED")
                return {"state": "CACHED", "result": json.loads(previous["result_json"])}
            if self._unconfirmed(connection, candidate_id):
                raise ControlError("VALIDATION_UNCONFIRMED")
            grant = connection.execute(
                "SELECT * FROM validation_grants WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if grant is None:
                raise ControlError("VALIDATION_APPROVAL_REQUIRED")
            if grant["expires_at"] <= time.time():
                raise ControlError("VALIDATION_APPROVAL_EXPIRED")
            if grant["used_runs"] >= grant["max_runs"]:
                raise ControlError("VALIDATION_BUDGET_EXHAUSTED")
            if grant["metadata_digest"] != metadata or (
                not grant["allow_edits"] and grant["digest"] != digest
            ):
                raise ControlError("VALIDATION_APPROVAL_CHANGED")
            token = uuid.uuid4().hex
            connection.execute(
                "UPDATE validation_grants SET used_runs=used_runs+1 WHERE candidate_id=?",
                (candidate_id,),
            )
            connection.execute(
                "INSERT INTO validation_runs "
                "(request_key,candidate_id,digest,token,status,created_at) VALUES(?,?,?,?,?,?)",
                (key, candidate_id, digest, token, "RUNNING", time.time()),
            )
        return {"state": "AUTHORIZED", "run_token": token, "candidate_hash": digest}

    def record_result(
        self, candidate_id: str, digest: str, key: str, run_token: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        invalid = False
        try:
            bounded = _bound_result(result, digest)
        except (ControlError, AttributeError, TypeError):
            bounded = {"status": "UNKNOWN", "candidate_hash": digest,
                       "code": "VALIDATION_RECEIPT_INVALID"}
            invalid = True
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT * FROM validation_runs WHERE request_key=?", (key,)
            ).fetchone()
            if (
                run is None or run["candidate_id"] != candidate_id or run["digest"] != digest
                or run["token"] != run_token
            ):
                raise ControlError("VALIDATION_RUN_MISMATCH")
            if run["status"] != "RUNNING":
                if not invalid and json.loads(run["result_json"]) == bounded:
                    return bounded
                raise ControlError("VALIDATION_RUN_ALREADY_FINISHED")
            connection.execute(
                "UPDATE validation_runs SET status=?,result_json=? WHERE request_key=?",
                (bounded["status"], json.dumps(bounded, ensure_ascii=False), key),
            )
        if invalid:
            raise ControlError("VALIDATION_RECEIPT_INVALID")
        return bounded

    def latest_pass(self, candidate_id: str, digest: str) -> dict[str, Any] | None:
        self._candidate(candidate_id, digest)
        with self._connect() as connection:
            connection.execute("BEGIN")
            # An unresolved run of another snapshot still belongs to this
            # candidate; editing back cannot revive an earlier safe-to-install result.
            if self._unconfirmed(connection, candidate_id):
                return None
            row = connection.execute(
                "SELECT status,result_json FROM validation_runs "
                "WHERE candidate_id=? AND digest=? "
                "ORDER BY rowid DESC LIMIT 1", (candidate_id, digest),
            ).fetchone()
        if row is None or row["status"] != "PASSED":
            return None
        return _bound_result(json.loads(row["result_json"]), digest)

    def status(self, candidate_id: str) -> dict[str, Any]:
        self.workspace.candidate_path(candidate_id)
        with self._connect() as connection:
            grant = connection.execute(
                "SELECT * FROM validation_grants WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            rows = connection.execute(
                "SELECT digest,status,result_json FROM validation_runs WHERE candidate_id=? "
                "ORDER BY rowid DESC LIMIT 10", (candidate_id,),
            ).fetchall()
        public_grant = None
        if grant:
            public_grant = {
                "candidate_hash": grant["digest"], "expires_at": grant["expires_at"],
                "allow_edits": bool(grant["allow_edits"]),
                "remaining_runs": max(0, grant["max_runs"] - grant["used_runs"]),
                "expired": grant["expires_at"] <= time.time(),
            }
        return {
            "candidate_id": candidate_id, "grant": public_grant,
            "runs": [
                {"candidate_hash": row["digest"], "status": row["status"],
                 "result": json.loads(row["result_json"]) if row["result_json"] else None}
                for row in rows
            ],
        }


Control = ControlStore
