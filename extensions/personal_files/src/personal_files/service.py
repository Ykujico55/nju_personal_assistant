"""Instance-scoped file plans and durable, conservative mutation receipts.

SQLite serializes cooperating plugin processes. It does NOT turn filesystem
hash-check + replace into an atomic CAS against arbitrary external editors.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote

from personal_files.errors import FilesError
from personal_files.paths import FileAccess

MAX_TEXT_BYTES = 262144
_HASH = re.compile(r"[0-9a-f]{64}\Z")
# These failures occur before touching the target. All other mutation failures
# are conservatively UNKNOWN, including failed receipt commits after replacement.
_PREWRITE_FAILURES = {"VERSION_CONFLICT", "READ_ONLY", "PLAN_EXPIRED", "CONFIGURATION_CHANGED"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _key(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise FilesError("INVALID_COMMAND_KEY")
    return value


def _page(offset: int, limit: int) -> None:
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 262144:
        raise FilesError("INVALID_PAGE")


class FileService:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.access = FileAccess(config)

    def roots(self) -> dict[str, Any]:
        return {"roots": self.access.roots_view(), "scope": "assistant_instance"}

    def list(
        self, root_key: str, path: str = "", offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        return self.access.list(root_key, path, offset=offset, limit=limit)

    def read(self, root_key: str, path: str, offset: int = 0, limit: int = 65536) -> dict[str, Any]:
        _page(offset, limit)
        item = self.access.snapshot(root_key, path)
        if item is None:
            raise FilesError("FILE_NOT_FOUND")
        end = min(len(item.content), offset + limit)
        if offset > len(item.content):
            raise FilesError("INVALID_PAGE")
        return {
            "root_key": root_key,
            "path": path,
            "source_uri": f"files://{root_key}/{quote(path, safe='/')}",
            "content": item.content[offset:end],
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
            "offset": offset,
            "end_offset": end,
            "offset_unit": "unicode_characters",
            "eof": end == len(item.content),
            "next_offset": end if end < len(item.content) else None,
            "sensitivity": "SENSITIVE",
            "trust": "USER_SOURCE",
        }

    @contextmanager
    def _db(self, *, create: bool = True) -> Iterator[sqlite3.Connection]:
        self.access.validate_state()
        path = self.access.state_directory / "files.sqlite3"
        if not create and not path.exists():
            raise FilesError("PLAN_NOT_FOUND")
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(
                path.as_uri() + "?mode=ro" if not create else str(path),
                uri=not create,
                timeout=2,
                isolation_level=None,
            )
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            if create:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, "
                    "body TEXT NOT NULL, status TEXT NOT NULL, receipt TEXT)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS commands (key TEXT PRIMARY KEY, "
                    "fingerprint TEXT NOT NULL, plan_id TEXT NOT NULL, "
                    "status TEXT NOT NULL, result TEXT)"
                )
            yield db
        except sqlite3.OperationalError as exc:
            code = "STATE_BUSY" if "locked" in str(exc).lower() else "STATE_UNAVAILABLE"
            raise FilesError(code) from None
        except sqlite3.DatabaseError:
            raise FilesError("STATE_UNAVAILABLE") from None
        finally:
            if db is not None:
                db.close()

    @contextmanager
    def _transaction(self, db: sqlite3.Connection) -> Iterator[None]:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield
            db.commit()
        except BaseException:
            db.rollback()
            raise

    def _plan(
        self, db: sqlite3.Connection, plan_id: str, plan_hash: str | None = None
    ) -> tuple[dict[str, Any], sqlite3.Row]:
        row = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise FilesError("PLAN_NOT_FOUND")
        body: dict[str, Any] = json.loads(row["body"])
        computed = _hash({k: v for k, v in body.items() if k != "plan_hash"})
        if computed != body["plan_hash"]:
            raise FilesError("STATE_INVALID")
        if plan_hash is not None and plan_hash != body["plan_hash"]:
            raise FilesError("PLAN_HASH_MISMATCH")
        if body["configuration_hash"] != self.access.configuration_hash:
            raise FilesError("CONFIGURATION_CHANGED")
        return body, row

    @staticmethod
    def _public_plan(body: dict[str, Any]) -> dict[str, Any]:
        return {
            k: v
            for k, v in body.items()
            if k not in {"content", "configuration_hash", "target_key"}
        }

    @staticmethod
    def _ensure_target_resolved(
        db: sqlite3.Connection, target_key: str, except_plan: str = ""
    ) -> None:
        for pending in db.execute("SELECT id, body FROM plans WHERE status='APPLYING'"):
            if (
                pending["id"] != except_plan
                and json.loads(pending["body"])["target_key"] == target_key
            ):
                raise FilesError("TARGET_RESULT_UNKNOWN")

    @staticmethod
    def _replay(db: sqlite3.Connection, key: str, fingerprint: str) -> dict[str, Any] | None:
        row = db.execute("SELECT * FROM commands WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["fingerprint"] != fingerprint:
            raise FilesError("IDEMPOTENCY_CONFLICT")
        if row["status"] == "FAILED":
            raise FilesError(json.loads(row["result"])["error_code"])
        if row["status"] == "APPLYING":
            return {
                "status": "UNKNOWN",
                "plan_id": row["plan_id"],
                "error_code": "FILE_RESULT_UNKNOWN",
                "automatic_retry": False,
            }
        result: dict[str, Any] = json.loads(row["result"])
        return result

    def preview(
        self,
        operation: str,
        root_key: str,
        path: str,
        expected_sha256: str | None,
        content: str | None = None,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = _key(idempotency_key)
        if operation not in {"write", "delete", "mkdir"}:
            raise FilesError("OPERATION_INVALID")
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str) or not _HASH.fullmatch(expected_sha256)
        ):
            raise FilesError("HASH_REQUIRED")
        if operation == "write":
            if not isinstance(content, str) or "\x00" in content:
                raise FilesError("TEXT_REQUIRED")
            try:
                data = content.encode("utf-8")
            except UnicodeError:
                raise FilesError("TEXT_REQUIRED") from None
            if len(data) > MAX_TEXT_BYTES:
                raise FilesError("FILE_TOO_LARGE")
        elif operation == "mkdir":
            if content is not None or expected_sha256 is not None:
                raise FilesError("DIRECTORY_INPUT_INVALID")
        elif content is not None or expected_sha256 is None:
            raise FilesError("DELETE_INPUT_INVALID")
        self.access.ensure_writable(root_key)
        fingerprint = _hash(
            [
                "preview",
                operation,
                root_key,
                path,
                expected_sha256,
                content,
                self.access.configuration_hash,
            ]
        )
        with self._db() as db, self._transaction(db):
            replay = self._replay(db, key, fingerprint)
            if replay is not None:
                return replay
            if operation == "mkdir":
                self.access.directory_absent(root_key, path)
            target_key = self.access.target_key(root_key, path)
            self._ensure_target_resolved(db, target_key)
            old = (
                None
                if operation == "mkdir"
                else self.access.snapshot(root_key, path, missing_ok=True)
            )
            actual = old.sha256 if old else None
            if actual != expected_sha256:
                raise FilesError("VERSION_CONFLICT")
            new = content if operation == "write" else ""
            assert new is not None
            before = old.content if old else ""
            diff_lines = difflib.unified_diff(
                before.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"before/{root_key}/{path}",
                tofile=f"after/{root_key}/{path}",
            )
            diff = "".join(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in diff_lines
            )
            if operation == "mkdir":
                diff = f"Create directory: {root_key}/{path}\n"
            created = time.time()
            body = {
                "plan_id": "fileplan_" + uuid.uuid4().hex,
                "operation": operation,
                "root_key": root_key,
                "path": path,
                "before_sha256": actual,
                "after_sha256": hashlib.sha256(new.encode("utf-8")).hexdigest()
                if operation == "write"
                else None,
                "content": content,
                "diff": diff,
                "created_at": created,
                "expires_at": created + (300 if operation == "delete" else 900),
                "requires_local_confirmation": operation == "delete",
                "configuration_hash": self.access.configuration_hash,
                "target_key": target_key,
            }
            body["plan_hash"] = _hash(body)
            result = self._public_plan(body)
            db.execute(
                "INSERT INTO plans VALUES (?,?,?,NULL)", (body["plan_id"], _json(body), "PREVIEWED")
            )
            db.execute(
                "INSERT INTO commands VALUES (?,?,?,?,?)",
                (key, fingerprint, body["plan_id"], "PREVIEWED", _json(result)),
            )
            return result

    def status(self, plan_id: str) -> dict[str, Any]:
        with self._db(create=False) as db:
            body, row = self._plan(db, plan_id)
            status = "UNKNOWN" if row["status"] == "APPLYING" else row["status"]
            result = self._public_plan(body)
            result.update(
                status=status,
                receipt=json.loads(row["receipt"]) if row["receipt"] else None,
                receipt_scope="original_operation_not_current_file",
            )
            return result

    def write(self, plan_id: str, plan_hash: str, *, idempotency_key: str) -> dict[str, Any]:
        return self._apply(plan_id, plan_hash, idempotency_key, "write")

    def delete_confirmed(self, plan_id: str, plan_hash: str) -> dict[str, Any]:
        """Owner CLI only, after a real TTY confirmation; not an Agent tool/grant.

        Trusted local Python can call this method. This is not a sandbox boundary.
        """
        return self._apply(plan_id, plan_hash, "local-delete:" + plan_id, "delete")

    def _apply(self, plan_id: str, plan_hash: str, key: str, operation: str) -> dict[str, Any]:
        key = _key(key)
        fingerprint = _hash([operation, plan_id, plan_hash])
        with self._db() as db, self._transaction(db):
            replay = self._replay(db, key, fingerprint)
            if replay is not None:
                return replay
            body, row = self._plan(db, plan_id, plan_hash)
            if body["operation"] != operation and not (
                operation == "write" and body["operation"] == "mkdir"
            ):
                raise FilesError(
                    "LOCAL_CONFIRMATION_REQUIRED"
                    if body["operation"] == "delete"
                    else "OPERATION_INVALID"
                )
            if row["status"] != "PREVIEWED":
                raise FilesError("PLAN_ALREADY_USED")
            if time.time() > body["expires_at"]:
                raise FilesError("PLAN_EXPIRED")
            self.access.ensure_writable(body["root_key"])
            self._ensure_target_resolved(db, body["target_key"], plan_id)
            db.execute("UPDATE plans SET status='APPLYING' WHERE id=?", (plan_id,))
            db.execute(
                "INSERT INTO commands VALUES (?,?,?,?,NULL)",
                (key, fingerprint, plan_id, "APPLYING"),
            )
        try:
            # Keep this lock through filesystem mutation and receipt commit.
            with self._db() as db, self._transaction(db):
                body, _ = self._plan(db, plan_id, plan_hash)
                if time.time() > body["expires_at"]:
                    raise FilesError("PLAN_EXPIRED")
                if body["operation"] == "mkdir":
                    self.access.mkdir(body["root_key"], body["path"])
                    applied_hash: str | None = None
                elif operation == "write":
                    snapshot = self.access.write(
                        body["root_key"], body["path"], body["content"], body["before_sha256"]
                    )
                    applied_hash = snapshot.sha256
                else:
                    self.access.delete(body["root_key"], body["path"], body["before_sha256"])
                    applied_hash = None
                receipt = {
                    "status": "APPLIED",
                    "plan_id": plan_id,
                    "plan_hash": plan_hash,
                    "operation": body["operation"],
                    "root_key": body["root_key"],
                    "path": body["path"],
                    "before_sha256": body["before_sha256"],
                    "applied_sha256": applied_hash,
                    "completed_at": time.time(),
                    "receipt_scope": "original_operation_not_current_file",
                }
                self._record_applied(db, plan_id, key, receipt)
            return receipt
        except FilesError as exc:
            if exc.code in _PREWRITE_FAILURES:
                self._record_failed(plan_id, key, exc.code)
                raise
            return self._unknown(plan_id)
        except (OSError, sqlite3.Error):
            return self._unknown(plan_id)

    @staticmethod
    def _unknown(plan_id: str) -> dict[str, Any]:
        return {
            "status": "UNKNOWN",
            "plan_id": plan_id,
            "error_code": "FILE_RESULT_UNKNOWN",
            "automatic_retry": False,
        }

    @staticmethod
    def _record_applied(
        db: sqlite3.Connection, plan_id: str, key: str, receipt: dict[str, Any]
    ) -> None:
        db.execute(
            "UPDATE plans SET status='APPLIED', receipt=? WHERE id=?", (_json(receipt), plan_id)
        )
        db.execute(
            "UPDATE commands SET status='APPLIED', result=? WHERE key=?", (_json(receipt), key)
        )

    def _record_failed(self, plan_id: str, key: str, code: str) -> None:
        result = _json({"status": "FAILED", "error_code": code, "plan_id": plan_id})
        with self._db() as db, self._transaction(db):
            db.execute("UPDATE plans SET status='FAILED',receipt=? WHERE id=?", (result, plan_id))
            db.execute("UPDATE commands SET status='FAILED',result=? WHERE key=?", (result, key))
