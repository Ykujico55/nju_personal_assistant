"""Local SQLite persistence for the standalone planner; no host state access."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


class PlanConflict(ValueError):
    code = "PLAN_CONFLICT"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


class LocalStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, data TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS recipients (email TEXT PRIMARY KEY, data TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, "
            "dedupe_key TEXT UNIQUE NOT NULL,"
            "version INTEGER NOT NULL, state TEXT NOT NULL, digest TEXT NOT NULL,"
            "data TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS plan_revisions (plan_id TEXT NOT NULL,"
            "version INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(plan_id,version));"
        )

    def __enter__(self) -> LocalStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self._db.close()

    def import_jobs(self, jobs: Iterable[Mapping[str, Any]]) -> int:
        rows = [(str(job["job_id"]), _json(dict(job))) for job in jobs]
        with self._db:
            self._db.executemany(
                "INSERT INTO jobs VALUES (?,?) ON CONFLICT(job_id) "
                "DO UPDATE SET data=excluded.data", rows,
            )
        return len(rows)

    def jobs(self) -> list[dict[str, Any]]:
        return [json.loads(row[0]) for row in self._db.execute(
            "SELECT data FROM jobs ORDER BY job_id"
        )]

    def get_job(self, job_id: str) -> dict[str, Any]:
        row = self._db.execute("SELECT data FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("job not found")
        return json.loads(row[0])  # type: ignore[no-any-return]

    def import_recipients(self, recipients: Iterable[Mapping[str, Any]]) -> int:
        rows = [(str(item["email"]).casefold(), _json(dict(item))) for item in recipients]
        with self._db:
            self._db.executemany(
                "INSERT INTO recipients VALUES (?,?) ON CONFLICT(email) "
                "DO UPDATE SET data=excluded.data", rows,
            )
        return len(rows)

    def recipients(self) -> list[dict[str, Any]]:
        return [json.loads(row[0]) for row in self._db.execute(
            "SELECT data FROM recipients ORDER BY email"
        )]

    def save_plan(
        self, plan: Mapping[str, Any], *, expected_version: int | None = None,
    ) -> dict[str, Any]:
        key = plan.get("dedupe_key")
        state = plan.get("state")
        if not isinstance(key, str) or not key or not isinstance(state, str):
            raise ValueError("plan identity and state are required")
        if expected_version is not None and (
            type(expected_version) is not int or expected_version < 1
        ):
            raise ValueError("expected version must be a positive integer")
        data = _json(dict(plan))
        digest = hashlib.sha256(data.encode()).hexdigest()
        identifier = "plan_" + hashlib.sha256(key.encode()).hexdigest()[:24]
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            current = self._db.execute(
                "SELECT * FROM plans WHERE dedupe_key=?", (key,),
            ).fetchone()
            version = 1
            if current is not None:
                if current["digest"] == digest:
                    return self._view(current)
                if current["state"] not in {"PREPARED", "NEEDS_USER"}:
                    raise PlanConflict("an existing application state cannot be reset")
                if expected_version != current["version"]:
                    raise PlanConflict("plan changed; inspect it and supply expected version")
                version = int(current["version"]) + 1
            elif expected_version is not None:
                raise PlanConflict("the plan does not exist at that version")
            self._db.execute(
                "INSERT INTO plans VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "version=excluded.version,state=excluded.state,digest=excluded.digest,"
                "data=excluded.data", (identifier, key, version, state, digest, data),
            )
            self._db.execute(
                "INSERT INTO plan_revisions VALUES (?,?,?)", (identifier, version, data),
            )
        return self.get_plan(identifier)

    @staticmethod
    def _view(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "version": row["version"], "state": row["state"],
                "plan": json.loads(row["data"])}

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        row = self._db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise KeyError("plan not found")
        return self._view(row)

    def plans(self) -> list[dict[str, Any]]:
        return [self._view(row) for row in self._db.execute("SELECT * FROM plans ORDER BY id")]

    def plan_versions(self, plan_id: str) -> list[dict[str, Any]]:
        return [{"version": row[0], "plan": json.loads(row[1])} for row in self._db.execute(
            "SELECT version,data FROM plan_revisions WHERE plan_id=? ORDER BY version", (plan_id,),
        )]
