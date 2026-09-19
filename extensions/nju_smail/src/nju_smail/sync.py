"""Read-only IMAP synchronization with UIDVALIDITY/UID cursors and dedupe.

The extension asks the host broker for capability probes, folders and bounded
message fetches.  It never opens a socket itself and never issues a mutating IMAP
command.  Every batch persists messages, locations, threads, contacts, events and
the new cursor in a single host transaction before the batch is acknowledged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from personal_assistant_sdk import FetchedMail, HostCapabilityError, HostMailClient

from .mime import (
    canonical_content_hash,
    dedupe_key,
    parse_message,
    snippet,
)
from .models import (
    AccountConfig,
    ExtensionSettings,
    FolderSyncReport,
    MailConfigError,
    SyncReport,
)
from .store import MAX_BATCH_MESSAGES, AttachmentInsert, MailStore, MessageInsert

NEEDS_USER_ACTION_CODES = frozenset(
    {"MAIL_AUTH_FAILED", "MAIL_AUTH_UNSUPPORTED", "MAIL_CREDENTIAL_UNAVAILABLE"}
)
MAX_BACKOFF_SECONDS = 3600
BASE_BACKOFF_SECONDS = 60
MAX_FAILURES = 5
EVENT_TYPE = "mail.received"


class MailSynchronizer:
    def __init__(
        self,
        store: MailStore,
        host_mail: HostMailClient,
        settings: ExtensionSettings,
        *,
        migrations: Sequence[Mapping[str, Any]],
    ) -> None:
        self._store = store
        self._host_mail = host_mail
        self._settings = settings
        self._migrations = migrations
        self._schema_ready = False

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        await self._store.migrate(self._migrations)
        self._schema_ready = True

    async def sync(
        self,
        *,
        account_id: str | None = None,
        folder: str | None = None,
        force: bool = False,
    ) -> SyncReport:
        await self.ensure_schema()
        if not self._settings.configured:
            raise MailConfigError(
                "SMAL_AWAITING_CONFIGURATION", "no mail account is configured"
            )
        accounts = (
            tuple(item for item in self._settings.accounts if item.account_id == account_id)
            if account_id
            else self._settings.accounts
        )
        if account_id and not accounts:
            raise MailConfigError("SMAL_ACCOUNT_UNKNOWN", "the account is not configured")
        folders = (
            (folder,) if folder is not None else tuple(self._settings.folders)
        )
        report: list[FolderSyncReport] = []
        for account in accounts:
            report.extend(await self._sync_account(account, folders, force=force))
        return SyncReport(folders=tuple(report))

    async def _sync_account(
        self, account: AccountConfig, folders: Sequence[str], *, force: bool
    ) -> list[FolderSyncReport]:
        if not force:
            states = [
                await self._store.state(account.account_id, name) for name in folders
            ]
            if states and all(
                state is not None
                and (
                    str(state.get("status")) == "NEEDS_USER_ACTION"
                    or (
                        isinstance(state.get("backoff_until"), datetime)
                        and state["backoff_until"] > datetime.now(UTC)
                    )
                )
                for state in states
            ):
                # No hot retry loop for revoked credentials or transient
                # failures: the folder cursor already records the bounded wait.
                return [
                    FolderSyncReport(
                        account_id=account.account_id,
                        folder=name,
                        status=str(state.get("status") or "BACKOFF"),
                        error_code=str(state.get("error_code") or "MAIL_UNAVAILABLE"),
                    )
                    for name, state in zip(folders, states, strict=True)
                    if state is not None
                ]
        try:
            capabilities = await self._host_mail.probe(account.account_id)
            discovered = await self._host_mail.list_folders(account.account_id)
        except HostCapabilityError as exc:
            await self._record_failure(account, folders, exc)
            return [
                FolderSyncReport(
                    account_id=account.account_id,
                    folder=name,
                    status=_failure_status(exc),
                    error_code=_safe_code(exc),
                )
                for name in folders
            ]
        del capabilities
        for discovered_folder in discovered:
            await self._store.record_folder(
                account.account_id,
                {
                    "name": discovered_folder.name,
                    "delimiter": discovered_folder.delimiter,
                    "attributes": list(discovered_folder.attributes),
                    "selectable": discovered_folder.selectable,
                },
            )
        reports: list[FolderSyncReport] = []
        for name in folders:
            reports.append(await self._sync_folder(account, name, force=force))
        return reports

    async def _sync_folder(
        self, account: AccountConfig, folder: str, *, force: bool
    ) -> FolderSyncReport:
        state = await self._store.state(account.account_id, folder)
        status = str(state.get("status", "")) if state is not None else ""
        if status == "NEEDS_USER_ACTION" and not force:
            return FolderSyncReport(
                account_id=account.account_id,
                folder=folder,
                status="NEEDS_USER_ACTION",
                error_code=str(
                    (state or {}).get("error_code") or "MAIL_AUTH_FAILED"
                ),
            )
        if state is not None and not force:
            backoff_until = state.get("backoff_until")
            if isinstance(backoff_until, datetime) and backoff_until > datetime.now(UTC):
                return FolderSyncReport(
                    account_id=account.account_id,
                    folder=folder,
                    status="BACKOFF",
                    error_code=str(state.get("error_code") or "MAIL_UNAVAILABLE"),
                )
        uidvalidity = int(state["uidvalidity"]) if state is not None else 0
        last_uid = int(state["last_uid"]) if state is not None else 0
        try:
            batch = await self._host_mail.fetch(
                account.account_id,
                folder,
                uidvalidity=uidvalidity or None,
                start_uid=last_uid + 1 if uidvalidity else 0,
                limit=self._settings.max_messages_per_sync,
            )
        except HostCapabilityError as exc:
            await self._record_failure(account, (folder,), exc)
            return FolderSyncReport(
                account_id=account.account_id,
                folder=folder,
                status=_failure_status(exc),
                error_code=_safe_code(exc),
            )
        if uidvalidity and batch.uidvalidity and batch.uidvalidity != uidvalidity:
            # UIDVALIDITY reset: rebuild the cursor from zero under the new
            # identity.  Dedupe by Message-ID/content hash prevents duplicates
            # and no message is lost.
            await self._store.reset_cursor(account.account_id, folder, batch.uidvalidity)
            try:
                batch = await self._host_mail.fetch(
                    account.account_id,
                    folder,
                    uidvalidity=batch.uidvalidity,
                    start_uid=0,
                    limit=self._settings.max_messages_per_sync,
                )
            except HostCapabilityError as exc:
                await self._record_failure(account, (folder,), exc)
                return FolderSyncReport(
                    account_id=account.account_id,
                    folder=folder,
                    status=_failure_status(exc),
                    error_code=_safe_code(exc),
                )
        messages = batch.messages
        if not messages:
            if batch.uidvalidity:
                await self._store.set_state_idle(
                    account.account_id, folder, batch.uidvalidity, last_uid
                )
            return FolderSyncReport(
                account_id=account.account_id,
                folder=folder,
                status="IDLE",
                scanned=0,
            )
        new_messages = 0
        events = 0
        for start in range(0, len(messages), MAX_BATCH_MESSAGES):
            chunk = messages[start : start + MAX_BATCH_MESSAGES]
            # Only advance the cursor to this chunk's highest UID.  A failure in
            # a later chunk must leave the cursor at the last committed chunk so
            # the next sync refetches the skipped UIDs instead of losing mail.
            chunk_next_uid = max(item.uid for item in chunk)
            inserts = [
                _message_insert(
                    account.account_id, folder, batch.uidvalidity, item
                )
                for item in chunk
            ]
            chunk_new, chunk_events = await self._store.apply_batch(
                account_id=account.account_id,
                folder=folder,
                uidvalidity=batch.uidvalidity or 0,
                next_uid=chunk_next_uid,
                messages=inserts,
            )
            new_messages += chunk_new
            events += chunk_events
        return FolderSyncReport(
            account_id=account.account_id,
            folder=folder,
            status="IDLE",
            new_messages=new_messages,
            events=events,
            scanned=len(messages),
        )

    async def _record_failure(
        self,
        account: AccountConfig,
        folders: Sequence[str],
        exc: HostCapabilityError,
    ) -> None:
        code = _safe_code(exc)
        is_user_action = code in NEEDS_USER_ACTION_CODES
        now = datetime.now(UTC)
        for folder in folders:
            state = await self._store.state(account.account_id, folder)
            failures = int(state.get("failure_count", 0)) if state else 0
            next_failures = min(failures + 1, MAX_FAILURES)
            if is_user_action:
                await self._store.set_state_error(
                    account.account_id,
                    folder,
                    status="NEEDS_USER_ACTION",
                    error_code=code,
                    failure_count=next_failures,
                    backoff_until=None,
                )
                continue
            delay = min(
                BASE_BACKOFF_SECONDS * (2 ** max(0, next_failures - 1)),
                MAX_BACKOFF_SECONDS,
            )
            await self._store.set_state_error(
                account.account_id,
                folder,
                status="BACKOFF",
                error_code=code,
                failure_count=next_failures,
                backoff_until=(now + timedelta(seconds=delay)).isoformat(),
            )


def _message_insert(
    account_id: str,
    folder: str,
    uidvalidity: int,
    message: FetchedMail,
) -> MessageInsert:
    parsed = parse_message(message.raw)
    attachment_hashes = [item.sha256 for item in parsed.attachments]
    content_hash = canonical_content_hash(
        message_id=parsed.message_id,
        subject=parsed.subject,
        from_address=parsed.from_address,
        to_addresses=parsed.to_addresses,
        sent_at=parsed.sent_at,
        body_text=parsed.body_text,
        attachment_hashes=attachment_hashes,
        size_bytes=message.size_bytes,
        truncated=parsed.truncated or message.truncated,
    )
    identity = dedupe_key(message_id=parsed.message_id, content_hash=content_hash)
    message_pk = "mail_" + hashlib.sha256(
        f"{account_id}|{identity}".encode()
    ).hexdigest()[:40]
    thread_id = _thread_id(account_id, parsed)
    people: list[tuple[str, str, str]] = []
    if parsed.from_address:
        people.append(("from", parsed.from_address.lower(), parsed.from_name))
    for address in parsed.to_addresses:
        people.append(("to", address.lower(), ""))
    for address in parsed.cc_addresses:
        people.append(("cc", address.lower(), ""))
    seen_people: set[tuple[str, str]] = set()
    unique_people: list[tuple[str, str, str]] = []
    for role, address, name in people:
        if (role, address) in seen_people:
            continue
        seen_people.add((role, address))
        unique_people.append((role, address, name))
    attachments = tuple(
        AttachmentInsert(
            attachment_id="att_"
            + hashlib.sha256(
                f"{message_pk}|{index}|{item.sha256}".encode()
            ).hexdigest()[:40],
            filename=item.filename,
            media_type=item.media_type,
            size_bytes=item.size_bytes,
            sha256=item.sha256,
            part_index=index,
        )
        for index, item in enumerate(parsed.attachments)
    )
    return MessageInsert(
        message_pk=message_pk,
        dedupe_key=identity,
        message_id=parsed.message_id,
        content_hash=content_hash,
        subject=parsed.subject,
        normalized_subject=_normalized_subject(parsed.subject),
        snippet=snippet(parsed.body_text),
        from_address=parsed.from_address.lower() if parsed.from_address else None,
        from_name=parsed.from_name,
        sent_at=parsed.sent_at,
        thread_id=thread_id,
        truncated=parsed.truncated or message.truncated,
        uid=message.uid,
        uidvalidity=uidvalidity,
        flags=tuple(message.flags),
        size_bytes=message.size_bytes,
        has_attachments=bool(attachments),
        event_type=EVENT_TYPE,
        people=tuple(unique_people),
        attachments=attachments,
    )


def _thread_id(account_id: str, parsed: Any) -> str:
    root = ""
    if parsed.references:
        root = parsed.references[0].strip().lower()
    elif parsed.in_reply_to:
        root = parsed.in_reply_to.strip().lower()
    elif parsed.message_id:
        root = parsed.message_id.strip().lower()
    key = "ref:" + root if root else "subject:" + _normalized_subject(parsed.subject)
    if key == "subject:":
        # An empty subject only threads with itself when there is a reference.
        key = "message:" + hashlib.sha256(
            parsed.body_text[:64].encode("utf-8", "replace")
        ).hexdigest()
    return "thr_" + hashlib.sha256(f"{account_id}|{key}".encode()).hexdigest()[:32]


def _normalized_subject(value: str | None) -> str:
    from .mime import normalized_subject

    return normalized_subject(value)


def _failure_status(exc: HostCapabilityError) -> str:
    return "NEEDS_USER_ACTION" if _safe_code(exc) in NEEDS_USER_ACTION_CODES else "BACKOFF"


def _safe_code(exc: HostCapabilityError) -> str:
    code = getattr(exc, "code", "")
    if isinstance(code, str) and code.startswith("MAIL_") and len(code) <= 64:
        return code
    return "MAIL_UNAVAILABLE"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["EVENT_TYPE", "MailSynchronizer"]
