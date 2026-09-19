"""SQL repository for the smail extension.

All persistence goes through the host's generic extension data capability: the
extension never sees a connection string or credential.  Message identity is
independent of the IMAP UID so repeated syncs, UIDVALIDITY resets and equal
content under different UIDs converge to one logical message and one event.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from personal_assistant_sdk import HostDataClient

from .models import DraftConflictError

MAX_BATCH_MESSAGES = 25
PROJECTABLE_STATES = frozenset(
    {"SUCCEEDED", "PARTIAL", "FAILED", "UNKNOWN", "SENT_CONFIRMED", "NEEDS_USER_ACTION"}
)

@dataclass(frozen=True, slots=True)
class AttachmentInsert:
    attachment_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    part_index: int


@dataclass(frozen=True, slots=True)
class MessageInsert:
    message_pk: str
    dedupe_key: str
    message_id: str | None
    content_hash: str
    subject: str
    normalized_subject: str
    snippet: str
    from_address: str | None
    from_name: str
    sent_at: str | None
    thread_id: str
    truncated: bool
    uid: int
    uidvalidity: int
    flags: tuple[str, ...]
    size_bytes: int
    has_attachments: bool
    event_type: str
    people: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)
    attachments: tuple[AttachmentInsert, ...] = field(default_factory=tuple)


class MailStore:
    def __init__(self, data: HostDataClient) -> None:
        self._data = data

    async def migrate(self, migrations: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        return await self._data.migrate(migrations)

    async def record_folder(self, account_id: str, folder: Mapping[str, Any]) -> None:
        await self._data.execute(
            "INSERT INTO mail_folders (account_id, folder_name, delimiter, attributes, "
            "selectable, last_seen_at) VALUES ($1, $2, $3, $4::jsonb, $5, now()) "
            "ON CONFLICT (account_id, folder_name) DO UPDATE SET "
            "delimiter = EXCLUDED.delimiter, attributes = EXCLUDED.attributes, "
            "selectable = EXCLUDED.selectable, last_seen_at = now()",
            [
                account_id,
                folder["name"],
                folder.get("delimiter"),
                json.dumps(list(folder.get("attributes", [])), separators=(",", ":")),
                bool(folder.get("selectable", True)),
            ],
        )

    async def state(self, account_id: str, folder: str) -> dict[str, Any] | None:
        rows = await self._fetch(
            "SELECT uidvalidity, last_uid, status, error_code, failure_count, "
            "backoff_until FROM mail_sync_state WHERE account_id = $1 AND folder_name = $2",
            [account_id, folder],
        )
        return rows[0] if rows else None

    async def set_state_idle(
        self, account_id: str, folder: str, uidvalidity: int, last_uid: int
    ) -> None:
        await self._data.execute(
            "INSERT INTO mail_sync_state (account_id, folder_name, uidvalidity, last_uid, "
            "status, last_sync_at, updated_at) VALUES ($1, $2, $3, $4, 'IDLE', now(), now()) "
            "ON CONFLICT (account_id, folder_name) DO UPDATE SET "
            "uidvalidity = EXCLUDED.uidvalidity, last_uid = EXCLUDED.last_uid, "
            "status = 'IDLE', error_code = NULL, failure_count = 0, "
            "backoff_until = NULL, last_sync_at = now(), updated_at = now()",
            [account_id, folder, uidvalidity, last_uid],
        )

    async def reset_cursor(self, account_id: str, folder: str, uidvalidity: int) -> None:
        await self._data.execute(
            "INSERT INTO mail_sync_state (account_id, folder_name, uidvalidity, last_uid, "
            "status, updated_at) VALUES ($1, $2, $3, 0, 'RESET', now()) "
            "ON CONFLICT (account_id, folder_name) DO UPDATE SET "
            "uidvalidity = EXCLUDED.uidvalidity, last_uid = 0, status = 'RESET', "
            "updated_at = now()",
            [account_id, folder, uidvalidity],
        )

    async def set_state_error(
        self,
        account_id: str,
        folder: str,
        *,
        status: str,
        error_code: str,
        failure_count: int,
        backoff_until: str | None,
    ) -> None:
        await self._data.execute(
            "INSERT INTO mail_sync_state (account_id, folder_name, status, error_code, "
            "failure_count, backoff_until, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::text::timestamptz, now()) "
            "ON CONFLICT (account_id, folder_name) DO UPDATE SET "
            "status = EXCLUDED.status, error_code = EXCLUDED.error_code, "
            "failure_count = EXCLUDED.failure_count, "
            "backoff_until = EXCLUDED.backoff_until, updated_at = now()",
            [account_id, folder, status, error_code, failure_count, backoff_until],
        )

    async def apply_batch(
        self,
        *,
        account_id: str,
        folder: str,
        uidvalidity: int,
        next_uid: int,
        messages: Sequence[MessageInsert],
    ) -> tuple[int, int]:
        """Persist messages, locations, threads, contacts, events and the cursor
        in one transaction.  The batch is only complete when it commits."""

        statements: list[dict[str, Any]] = []
        message_insert_indexes: list[int] = []
        for message in messages:
            message_insert_indexes.append(len(statements))
            statements.append(
                {
                    "statement": (
                        "INSERT INTO mail_messages (message_pk, account_id, dedupe_key, "
                        "message_id, content_hash, subject, normalized_subject, snippet, "
                        "from_address, from_name, sent_at, has_attachments, truncated, "
                        "thread_id) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, "
                        "$11::text::timestamptz, $12, $13, $14) "
                        "ON CONFLICT (account_id, dedupe_key) DO NOTHING "
                        "RETURNING message_pk"
                    ),
                    "parameters": [
                        message.message_pk,
                        account_id,
                        message.dedupe_key,
                        message.message_id,
                        message.content_hash,
                        message.subject,
                        message.normalized_subject,
                        message.snippet,
                        message.from_address,
                        message.from_name,
                        message.sent_at,
                        message.has_attachments,
                        message.truncated,
                        message.thread_id,
                    ],
                }
            )
            statements.append(
                {
                    "statement": (
                        "INSERT INTO mail_message_locations (account_id, folder_name, "
                        "uidvalidity, uid, message_pk, flags, size_bytes) "
                        "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7) "
                        "ON CONFLICT (account_id, folder_name, uidvalidity, uid) "
                        "DO UPDATE SET message_pk = EXCLUDED.message_pk, "
                        "flags = EXCLUDED.flags, size_bytes = EXCLUDED.size_bytes, "
                        "seen_at = now()"
                    ),
                    "parameters": [
                        account_id,
                        folder,
                        uidvalidity,
                        message.uid,
                        message.message_pk,
                        json.dumps(list(message.flags), separators=(",", ":")),
                        message.size_bytes,
                    ],
                }
            )
            for role, address, name in message.people:
                statements.append(
                    {
                        "statement": (
                            "INSERT INTO mail_message_people "
                            "(message_pk, role, address, display_name) "
                            "VALUES ($1, $2, $3, $4) "
                            "ON CONFLICT (message_pk, role, address) DO NOTHING"
                        ),
                        "parameters": [message.message_pk, role, address, name],
                    }
                )
                if role in {"from", "to", "cc"}:
                    statements.append(
                        {
                            "statement": (
                                "INSERT INTO mail_contacts (account_id, address, "
                                "display_name, last_seen_at) VALUES ($1, $2, $3, now()) "
                                "ON CONFLICT (account_id, address) DO UPDATE SET "
                                "last_seen_at = now(), display_name = CASE "
                                "WHEN EXCLUDED.display_name <> '' THEN EXCLUDED.display_name "
                                "ELSE mail_contacts.display_name END"
                            ),
                            "parameters": [account_id, address, name],
                        }
                    )
            for attachment in message.attachments:
                statements.append(
                    {
                        "statement": (
                            "INSERT INTO mail_attachments (attachment_id, message_pk, "
                            "filename, media_type, size_bytes, sha256, part_index) "
                            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
                            "ON CONFLICT (attachment_id) DO NOTHING"
                        ),
                        "parameters": [
                            attachment.attachment_id,
                            message.message_pk,
                            attachment.filename,
                            attachment.media_type,
                            attachment.size_bytes,
                            attachment.sha256,
                            attachment.part_index,
                        ],
                    }
                )
            statements.append(
                {
                    "statement": (
                        "INSERT INTO mail_threads (thread_id, account_id, "
                        "normalized_subject, last_message_at) VALUES ($1, $2, $3, "
                        "$4::text::timestamptz) ON CONFLICT (thread_id) DO UPDATE SET "
                        "last_message_at = CASE "
                        "WHEN EXCLUDED.last_message_at IS NULL THEN mail_threads.last_message_at "
                        "WHEN mail_threads.last_message_at IS NULL "
                        "OR EXCLUDED.last_message_at > mail_threads.last_message_at "
                        "THEN EXCLUDED.last_message_at ELSE mail_threads.last_message_at END"
                    ),
                    "parameters": [
                        message.thread_id,
                        account_id,
                        message.normalized_subject,
                        message.sent_at,
                    ],
                }
            )
            statements.append(
                {
                    "statement": (
                        "INSERT INTO mail_events (event_type, dedupe_key, account_id, "
                        "folder_name, message_pk, payload) "
                        "VALUES ($1, $2, $3, $4, $5, $6::jsonb) "
                        "ON CONFLICT (dedupe_key) DO NOTHING"
                    ),
                    "parameters": [
                        message.event_type,
                        f"smail:{account_id}:{message.dedupe_key}",
                        account_id,
                        folder,
                        message.message_pk,
                        json.dumps(
                            {
                                "message_id": message.message_id,
                                "dedupe_key": message.dedupe_key,
                                "content_hash": message.content_hash,
                                "subject": message.subject,
                                "from_address": message.from_address,
                                "sent_at": message.sent_at,
                                "uid": message.uid,
                                "uidvalidity": uidvalidity,
                                "folder": folder,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ],
                }
            )
        statements.append(
            {
                "statement": (
                    "INSERT INTO mail_sync_state (account_id, folder_name, uidvalidity, "
                    "last_uid, status, last_sync_at, updated_at) "
                    "VALUES ($1, $2, $3, $4, 'IDLE', now(), now()) "
                    "ON CONFLICT (account_id, folder_name) DO UPDATE SET "
                    "uidvalidity = EXCLUDED.uidvalidity, last_uid = EXCLUDED.last_uid, "
                    "status = 'IDLE', error_code = NULL, failure_count = 0, "
                    "backoff_until = NULL, last_sync_at = now(), updated_at = now()"
                ),
                "parameters": [account_id, folder, uidvalidity, next_uid],
            }
        )
        response = await self._data.transaction(statements)
        results = response.get("results")
        if not isinstance(results, list) or len(results) != len(statements):
            raise RuntimeError(
                "SMAL_BATCH_RESULT_INVALID: the host returned an unexpected shape"
            )
        new_messages = 0
        for index in message_insert_indexes:
            if _result_rows(results, index):
                new_messages += 1
        return new_messages, new_messages

    async def search(
        self, query: str, *, limit: int, account_id: str | None = None
    ) -> list[dict[str, Any]]:
        pattern = f"%{query}%"
        if account_id:
            return await self._fetch(
                "SELECT m.message_pk, m.account_id, m.message_id, m.content_hash, "
                "m.subject, m.snippet, m.from_address, m.from_name, m.sent_at, "
                "m.thread_id, l.folder_name, l.uid, l.uidvalidity "
                "FROM mail_messages m "
                "LEFT JOIN mail_message_locations l ON l.message_pk = m.message_pk "
                "WHERE m.account_id = $1 AND (m.subject ILIKE $2 OR m.snippet ILIKE $2) "
                "ORDER BY m.sent_at DESC NULLS LAST, m.message_pk LIMIT $3",
                [account_id, pattern, limit],
            )
        return await self._fetch(
            "SELECT m.message_pk, m.account_id, m.message_id, m.content_hash, "
            "m.subject, m.snippet, m.from_address, m.from_name, m.sent_at, "
            "m.thread_id, l.folder_name, l.uid, l.uidvalidity "
            "FROM mail_messages m "
            "LEFT JOIN mail_message_locations l ON l.message_pk = m.message_pk "
            "WHERE m.subject ILIKE $1 OR m.snippet ILIKE $1 "
            "ORDER BY m.sent_at DESC NULLS LAST, m.message_pk LIMIT $2",
            [pattern, limit],
        )

    async def thread_messages(
        self, account_id: str, thread_id: str, *, limit: int
    ) -> list[dict[str, Any]]:
        return await self._fetch(
            "SELECT m.message_pk, m.account_id, m.message_id, m.content_hash, "
            "m.subject, m.snippet, m.from_address, m.from_name, m.sent_at, "
            "m.thread_id, l.folder_name, l.uid, l.uidvalidity "
            "FROM mail_messages m "
            "LEFT JOIN mail_message_locations l ON l.message_pk = m.message_pk "
            "WHERE m.account_id = $1 AND m.thread_id = $2 "
            "ORDER BY m.sent_at ASC NULLS FIRST, m.message_pk LIMIT $3",
            [account_id, thread_id, limit],
        )

    async def events_after(self, after: int, *, limit: int) -> list[dict[str, Any]]:
        rows = await self._fetch(
            "SELECT sequence, event_type, dedupe_key, account_id, folder_name, "
            "message_pk, payload, created_at FROM mail_events "
            "WHERE sequence > $1 ORDER BY sequence ASC LIMIT $2",
            [after, limit],
        )
        for row in rows:
            row["payload"] = _decode_json(row.get("payload")) or {}
        return rows

    async def latest_sequence(self) -> int:
        rows = await self._fetch(
            "SELECT COALESCE(max(sequence), 0) AS sequence FROM mail_events", []
        )
        return int(rows[0]["sequence"]) if rows else 0

    # -- drafts -------------------------------------------------------------

    async def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        rows = await self._fetch(
            "SELECT draft_id, account_id, current_version, thread_id FROM mail_drafts "
            "WHERE draft_id = $1",
            [draft_id],
        )
        return rows[0] if rows else None

    async def get_draft_version(self, draft_id: str, version: int) -> dict[str, Any] | None:
        rows = await self._fetch(
            "SELECT draft_id, version, account_id, account_fingerprint, from_address, "
            "to_json, cc_json, bcc_json, subject, body_text, body_html, "
            "attachment_manifest, canonical_digest, mime_sha256, mime_artifact_id, "
            "local_action_id, revision_request_id, message_id, in_reply_to, refs_json, "
            "thread_id FROM mail_draft_versions WHERE draft_id = $1 AND version = $2",
            [draft_id, version],
        )
        if not rows:
            return None
        row = rows[0]
        for column in ("to_json", "cc_json", "bcc_json", "attachment_manifest", "refs_json"):
            row[column] = _decode_json(row.get(column))
        return row

    async def get_draft_version_by_request(
        self, draft_id: str, revision_request_id: str
    ) -> dict[str, Any] | None:
        rows = await self._fetch(
            "SELECT draft_id, version, account_id, account_fingerprint, from_address, "
            "to_json, cc_json, bcc_json, subject, body_text, body_html, "
            "attachment_manifest, canonical_digest, mime_sha256, mime_artifact_id, "
            "local_action_id, revision_request_id, message_id, in_reply_to, refs_json, "
            "thread_id FROM mail_draft_versions "
            "WHERE draft_id = $1 AND revision_request_id = $2",
            [draft_id, revision_request_id],
        )
        if not rows:
            return None
        row = rows[0]
        for column in ("to_json", "cc_json", "bcc_json", "attachment_manifest", "refs_json"):
            row[column] = _decode_json(row.get(column))
        return row

    async def insert_draft_version(
        self,
        *,
        draft_id: str,
        account_id: str,
        account_fingerprint: str,
        from_address: str,
        thread_id: str | None,
        to: Sequence[str],
        cc: Sequence[str],
        bcc: Sequence[str],
        subject: str,
        body_text: str,
        body_html: str | None,
        attachment_manifest: Sequence[Mapping[str, Any]],
        canonical_digest: str,
        mime_sha256: str,
        mime_artifact_id: str,
        local_action_id: str,
        revision_request_id: str,
        message_id: str,
        in_reply_to: str | None,
        references: Sequence[str],
        expected_current: int,
    ) -> int:
        """Atomically ensure the draft, CAS the pointer and insert the version.

        The version insert and the pointer CAS are one data-modifying CTE: if
        the CAS does not match (or the revision request already exists) the
        statement inserts nothing and moves nothing, so a conflict can never
        leave an orphan version row referencing a deleted artifact.
        """

        statements: list[dict[str, Any]] = [
            {
                "statement": (
                    "INSERT INTO mail_drafts (draft_id, account_id, current_version, thread_id) "
                    "VALUES ($1, $2, 0, $3) ON CONFLICT (draft_id) DO NOTHING"
                ),
                "parameters": [draft_id, account_id, thread_id],
            },
            {
                "statement": (
                    "WITH target AS ("
                    "SELECT current_version FROM mail_drafts WHERE draft_id = $1 FOR UPDATE"
                    "), inserted AS ("
                    "INSERT INTO mail_draft_versions (draft_id, version, account_id, "
                    "account_fingerprint, from_address, to_json, cc_json, bcc_json, subject, "
                    "body_text, body_html, attachment_manifest, canonical_digest, mime_sha256, "
                    "mime_artifact_id, local_action_id, revision_request_id, message_id, "
                    "in_reply_to, refs_json, thread_id) "
                    "SELECT $1, COALESCE((SELECT max(version) FROM mail_draft_versions "
                    "WHERE draft_id = $1), 0) + 1, $2, $3, $4, $5::jsonb, $6::jsonb, "
                    "$7::jsonb, $8, $9, $10, $11::jsonb, $12, $13, $14, $15, $16, $17, "
                    "$18, $19::jsonb, $20 FROM target "
                    "WHERE target.current_version = $21 "
                    "AND NOT EXISTS (SELECT 1 FROM mail_draft_versions "
                    "WHERE draft_id = $1 AND revision_request_id = $16) "
                    "RETURNING version"
                    "), pointer AS ("
                    "UPDATE mail_drafts SET current_version = (SELECT version FROM inserted), "
                    "thread_id = $20, updated_at = now() "
                    "WHERE draft_id = $1 AND EXISTS (SELECT 1 FROM inserted) "
                    "RETURNING current_version"
                    ") SELECT (SELECT version FROM inserted) AS version, "
                    "(SELECT count(*) FROM pointer) AS updated"
                ),
                "parameters": [
                    draft_id,
                    account_id,
                    account_fingerprint,
                    from_address,
                    json.dumps(list(to), ensure_ascii=False),
                    json.dumps(list(cc), ensure_ascii=False),
                    json.dumps(list(bcc), ensure_ascii=False),
                    subject,
                    body_text,
                    body_html,
                    json.dumps(list(attachment_manifest), ensure_ascii=False),
                    canonical_digest,
                    mime_sha256,
                    mime_artifact_id,
                    local_action_id,
                    revision_request_id,
                    message_id,
                    in_reply_to,
                    json.dumps(list(references), ensure_ascii=False),
                    thread_id,
                    expected_current,
                ],
            },
        ]
        response = await self._data.transaction(statements)
        results = response.get("results")
        if not isinstance(results, list) or len(results) != len(statements):
            raise RuntimeError("SMAL_DRAFT_RESULT_INVALID: unexpected host response")
        rows = _result_rows(results, 1)
        if not rows or rows[0].get("version") is None or int(rows[0].get("updated") or 0) != 1:
            raise DraftConflictError(
                "SMAL_DRAFT_CONFLICT", "the draft was edited concurrently"
            )
        return int(rows[0]["version"])

    # -- send actions -------------------------------------------------------

    async def get_action(self, local_action_id: str) -> dict[str, Any] | None:
        rows = await self._fetch(
            "SELECT local_action_id, account_id, draft_id, draft_version, message_id, "
            "envelope_digest, state, receipt, recipient_results FROM mail_send_actions "
            "WHERE local_action_id = $1",
            [local_action_id],
        )
        if not rows:
            return None
        row = rows[0]
        row["receipt"] = _decode_json(row.get("receipt"))
        row["recipient_results"] = _decode_json(row.get("recipient_results")) or []
        return row

    async def prepare_action(
        self,
        *,
        local_action_id: str,
        account_id: str,
        draft_id: str,
        draft_version: int,
        message_id: str,
        envelope_digest: str,
    ) -> dict[str, Any]:
        await self._data.execute(
            "INSERT INTO mail_send_actions (local_action_id, account_id, draft_id, "
            "draft_version, message_id, envelope_digest, state) "
            "VALUES ($1, $2, $3, $4, $5, $6, 'PREPARED') "
            "ON CONFLICT (local_action_id) DO NOTHING",
            [
                local_action_id,
                account_id,
                draft_id,
                draft_version,
                message_id,
                envelope_digest,
            ],
        )
        action = await self.get_action(local_action_id)
        if action is None:
            raise RuntimeError("SMAL_ACTION_MISSING: the send action was not persisted")
        if action["envelope_digest"] != envelope_digest:
            raise ValueError("SMAL_IDEMPOTENCY_CONFLICT: the action id is bound elsewhere")
        return action

    async def project_status(
        self,
        *,
        local_action_id: str,
        state: str,
        receipt: Mapping[str, Any] | None,
        recipient_results: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        """Mirror a host-authoritative status into the local projection.

        The CAS only moves PREPARED/UNKNOWN rows and can never overwrite a
        terminal projection, so the extension cannot be used to forge a send
        result and first-terminal-wins is enforced in SQL.
        """

        if state not in PROJECTABLE_STATES:
            raise ValueError("SMAL_SEND_STATE_INVALID: unknown projection state")
        await self._data.execute(
            "UPDATE mail_send_actions SET state = $2, receipt = $3::jsonb, "
            "recipient_results = $4::jsonb, updated_at = now() "
            "WHERE local_action_id = $1 AND state IN ('PREPARED', 'UNKNOWN') "
            "AND state <> $2",
            [
                local_action_id,
                state,
                json.dumps(dict(receipt) if receipt is not None else None, ensure_ascii=False),
                json.dumps(list(recipient_results), ensure_ascii=False),
            ],
        )
        return await self.get_action(local_action_id)

    # -- internals ----------------------------------------------------------

    async def _fetch(
        self, statement: str, parameters: Sequence[Any]
    ) -> list[dict[str, Any]]:
        response = await self._data.execute(statement, list(parameters))
        rows = response.get("rows")
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows if isinstance(row, Mapping)]


__all__ = [
    "MAX_BATCH_MESSAGES",
    "AttachmentInsert",
    "MailStore",
    "MessageInsert",
]


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _result_rows(results: Sequence[Any], index: int) -> list[Mapping[str, Any]]:
    if index >= len(results):
        return []
    entry = results[index]
    if not isinstance(entry, Mapping):
        return []
    rows = entry.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]
