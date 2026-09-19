"""Host-owned mail account registry adapters.

The file registry is the production default until F09's Windows credential
backend lands next to it; it stores only non-secret metadata and a
``SecretHandle`` id, never a password.

Correctness properties:

* every write bumps a per-account generation that is never reused (revision
  tombstones survive deletion), so delete + recreate cannot resurrect an old
  approval or lease;
* writers hold a cross-process exclusive file lock for the whole
  read-modify-write, and ``dispatch_guard`` hands the transport the same lock
  for the DATA critical section, so "check the binding, then enter DATA" is
  atomic across processes;
* guards read the registry live on every check and fail closed on read errors;
  there is no polling cache.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from personal_assistant.core.extensions.async_utils import run_blocking
from personal_assistant.core.mail import (
    MailAccountNotFoundError,
    MailAccountRecord,
    MailSendLeaseGuard,
)

from .file_lock import ExclusiveFileLock
from .owners import MailAccountDispatchGuard, _ThreadLockAdapter

MAX_ACCOUNTS = 32
MAX_REGISTRY_BYTES = 256 * 1024


class InMemoryMailAccountRegistry:
    def __init__(self, records: tuple[MailAccountRecord, ...] = ()) -> None:
        self._records: dict[str, MailAccountRecord] = {}
        self._revisions: dict[str, int] = {}
        for record in records:
            self._records[record.account_id] = record
            self._revisions[record.account_id] = record.generation
        self._lock = threading.RLock()
        # Held across the DATA critical section: an in-flight submission and an
        # account mutation are serialized, so the binding check plus entering
        # DATA is atomic in-process as well.
        self._critical_lock = threading.RLock()

    async def resolve(self, account_id: str) -> MailAccountRecord:
        with self._lock:
            record = self._records.get(account_id)
        if record is None:
            raise MailAccountNotFoundError(account_id)
        return record

    async def list(self) -> tuple[MailAccountRecord, ...]:
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    async def upsert(self, record: MailAccountRecord) -> None:
        with self._mutation(), self._lock:
            if (
                record.account_id not in self._records
                and len(self._records) >= MAX_ACCOUNTS
            ):
                raise ValueError("too many registered mail accounts")
            previous = self._records.get(record.account_id)
            floor = self._revisions.get(record.account_id, -1)
            bumped = _bump(previous, record, floor)
            self._records[record.account_id] = bumped
            self._revisions[record.account_id] = bumped.generation

    async def replace_all(self, records: tuple[MailAccountRecord, ...]) -> None:
        if len(records) > MAX_ACCOUNTS:
            raise ValueError("too many registered mail accounts")
        identifiers = [item.account_id for item in records]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("account ids must be unique")
        with self._mutation(), self._lock:
            previous = self._records
            updated: dict[str, MailAccountRecord] = {}
            for item in records:
                floor = self._revisions.get(item.account_id, -1)
                bumped = _bump(previous.get(item.account_id), item, floor)
                updated[item.account_id] = bumped
                self._revisions[item.account_id] = bumped.generation
            self._records = updated

    async def delete(self, account_id: str) -> None:
        with self._mutation(), self._lock:
            self._records.pop(account_id, None)

    async def verify(self, lease: MailAccountRecord) -> bool:
        with self._lock:
            return _matches(self._records.get(lease.account_id), lease)

    def dispatch_guard(self, lease: MailAccountRecord) -> MailSendLeaseGuard:
        def _verify() -> bool:
            with self._lock:
                return _matches(self._records.get(lease.account_id), lease)

        return MailAccountDispatchGuard(_verify, _ThreadLockAdapter(self._critical_lock))

    @contextlib.contextmanager
    def _mutation(self) -> Iterator[None]:
        acquired = self._critical_lock.acquire(timeout=5.0)
        if not acquired:
            raise ValueError(
                "the mail account registry is busy with an in-flight submission"
            )
        try:
            yield
        finally:
            self._critical_lock.release()


class FileMailAccountRegistry:
    """Strict, atomic, host-owned JSON registry with cross-process locking."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._lock_path = self._path.with_name(self._path.name + ".lock")
        self._write_lock = threading.RLock()

    async def resolve(self, account_id: str) -> MailAccountRecord:
        for record in await self.list():
            if record.account_id == account_id:
                return record
        raise MailAccountNotFoundError(account_id)

    async def list(self) -> tuple[MailAccountRecord, ...]:
        records, _revisions = self._snapshot_locked()
        return records

    async def upsert(self, record: MailAccountRecord) -> None:
        # The whole read-modify-write (including the cross-process lock wait)
        # runs off the event loop, and ``run_blocking`` keeps the caller from
        # observing cancellation before the thread's commit has finished, so a
        # cancelled Admin request cannot mutate the registry afterwards.
        await run_blocking(self._upsert_blocking, record)

    def _upsert_blocking(self, record: MailAccountRecord) -> None:
        with ExclusiveFileLock(self._lock_path):
            records, revisions = self._snapshot_locked()
            table = {item.account_id: item for item in records}
            if record.account_id not in table and len(table) >= MAX_ACCOUNTS:
                raise ValueError("too many registered mail accounts")
            floor = revisions.get(record.account_id, -1)
            bumped = _bump(table.get(record.account_id), record, floor)
            table[record.account_id] = bumped
            revisions[record.account_id] = bumped.generation
            self._commit_locked(
                tuple(table[key] for key in sorted(table)), revisions
            )

    async def replace_all(self, records: tuple[MailAccountRecord, ...]) -> None:
        if len(records) > MAX_ACCOUNTS:
            raise ValueError("too many registered mail accounts")
        identifiers = [item.account_id for item in records]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("account ids must be unique")
        await run_blocking(self._replace_all_blocking, records)

    def _replace_all_blocking(self, records: tuple[MailAccountRecord, ...]) -> None:
        with ExclusiveFileLock(self._lock_path):
            previous_records, revisions = self._snapshot_locked()
            previous = {item.account_id: item for item in previous_records}
            updated: dict[str, MailAccountRecord] = {}
            for item in records:
                floor = revisions.get(item.account_id, -1)
                bumped = _bump(previous.get(item.account_id), item, floor)
                updated[item.account_id] = bumped
                revisions[item.account_id] = bumped.generation
            self._commit_locked(
                tuple(updated[key] for key in sorted(updated)), revisions
            )

    async def verify(self, lease: MailAccountRecord) -> bool:
        return self._snapshot_matches(lease)

    async def delete(self, account_id: str) -> None:
        await run_blocking(self._delete_blocking, account_id)

    def _delete_blocking(self, account_id: str) -> None:
        with ExclusiveFileLock(self._lock_path):
            records, revisions = self._snapshot_locked()
            remaining = {
                item.account_id: item
                for item in records
                if item.account_id != account_id
            }
            self._commit_locked(
                tuple(remaining[key] for key in sorted(remaining)), revisions
            )

    def dispatch_guard(self, lease: MailAccountRecord) -> MailSendLeaseGuard:
        return MailAccountDispatchGuard(
            lambda: self._snapshot_matches(lease), ExclusiveFileLock(self._lock_path)
        )

    def _snapshot_locked(
        self,
    ) -> tuple[tuple[MailAccountRecord, ...], dict[str, int]]:
        with self._write_lock:
            return self._read_state_locked()

    def _commit_locked(
        self, records: tuple[MailAccountRecord, ...], revisions: dict[str, int]
    ) -> None:
        with self._write_lock:
            self._write_state_locked(records, revisions)

    def _snapshot_matches(self, lease: MailAccountRecord) -> bool:
        # Live, fail-closed read: any read/parse error means "cannot confirm".
        try:
            records, _revisions = self._snapshot_locked()
        except Exception:  # noqa: BLE001
            return False
        return _matches(
            next((item for item in records if item.account_id == lease.account_id), None),
            lease,
        )

    def _read_state_locked(
        self,
    ) -> tuple[tuple[MailAccountRecord, ...], dict[str, int]]:
        if not self._path.is_file():
            return (), {}
        data = self._path.read_bytes()
        if len(data) > MAX_REGISTRY_BYTES:
            raise ValueError("the mail account registry file is too large")
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("the mail account registry file is invalid JSON") from exc
        if not isinstance(document, dict) or not set(document).issubset(
            {"accounts", "revisions"}
        ):
            raise ValueError("the mail account registry file has an unexpected shape")
        raw_accounts = document.get("accounts")
        if not isinstance(raw_accounts, list) or len(raw_accounts) > MAX_ACCOUNTS:
            raise ValueError("the mail account registry file has an invalid account list")
        parsed = tuple(_record(item) for item in raw_accounts)
        identifiers = [item.account_id for item in parsed]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("the mail account registry file has duplicate account ids")
        revisions = _revisions(document.get("revisions", {}))
        stored = {item.account_id: item.generation for item in parsed}
        for account_id, generation in stored.items():
            revisions[account_id] = max(revisions.get(account_id, -1), generation)
        return parsed, revisions

    def _write_state_locked(
        self, records: tuple[MailAccountRecord, ...], revisions: dict[str, int]
    ) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "accounts": [_document(record) for record in records],
            "revisions": {key: revisions[key] for key in sorted(revisions)},
        }
        encoded = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > MAX_REGISTRY_BYTES:
            raise ValueError("the mail account registry document is too large")
        temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._path)


def _matches(current: MailAccountRecord | None, lease: MailAccountRecord) -> bool:
    return (
        current is not None
        and current.generation == lease.generation
        and current.fingerprint() == lease.fingerprint()
    )


def _document(record: MailAccountRecord) -> dict[str, Any]:
    return {
        "account_id": record.account_id,
        "address": record.address,
        "imap_host": record.imap_host,
        "imap_port": record.imap_port,
        "smtp_host": record.smtp_host,
        "smtp_port": record.smtp_port,
        "secret_handle_id": record.secret_handle_id,
        "display_name": record.display_name,
        "read_enabled": record.read_enabled,
        "send_enabled": record.send_enabled,
        "tls_mode": record.tls_mode,
        "generation": record.generation,
    }


def _record(value: Any) -> MailAccountRecord:
    if not isinstance(value, dict):
        raise ValueError("each registry account must be an object")
    allowed = {
        "account_id",
        "address",
        "imap_host",
        "imap_port",
        "smtp_host",
        "smtp_port",
        "secret_handle_id",
        "display_name",
        "read_enabled",
        "send_enabled",
        "tls_mode",
        "generation",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown mail account registry keys: {sorted(unknown)}")
    try:
        return MailAccountRecord(
            account_id=_text(value, "account_id"),
            address=_text(value, "address"),
            imap_host=_text(value, "imap_host"),
            smtp_host=_text(value, "smtp_host"),
            secret_handle_id=_text(value, "secret_handle_id"),
            imap_port=_int(value.get("imap_port", 993)),
            smtp_port=_int(value.get("smtp_port", 465)),
            display_name=_str(value.get("display_name", ""), "display_name", 120),
            read_enabled=_bool(value.get("read_enabled", True), "read_enabled"),
            send_enabled=_bool(value.get("send_enabled", False), "send_enabled"),
            tls_mode=_str(value.get("tls_mode", "auto"), "tls_mode", 16),
            generation=_generation(value.get("generation", 0)),
        )
    except ValueError as exc:
        raise ValueError(f"invalid mail account registry entry: {exc}") from exc


def _text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return item.strip()


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    return int(value)


def _generation(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("generation must be a non-negative integer")
    return int(value)


def _revisions(value: Any) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("registry revisions must be an object")
    result: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError("registry revision keys must be account ids")
        result[key] = _generation(item)
    return result


def _bump(
    previous: MailAccountRecord | None,
    record: MailAccountRecord,
    revision_floor: int,
) -> MailAccountRecord:
    """Advance the generation monotonically; revisions are never reused."""

    candidates = [record.generation, revision_floor + 1, 0]
    if previous is not None:
        candidates.append(previous.generation + 1)
    return replace(record, generation=max(candidates))


def _bool(value: Any, field: str) -> bool:
    # Strict JSON types only: a string like "false" must fail closed rather
    # than being coerced to True.
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a JSON boolean")
    return value


def _str(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit or chr(0) in value:
        raise ValueError(f"{field} must be a string of at most {limit} characters")
    return value


__all__ = ["FileMailAccountRegistry", "InMemoryMailAccountRegistry", "MAX_ACCOUNTS"]
