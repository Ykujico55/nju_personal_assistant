"""Extension-owned bookkeeping stored in its own ``ext_*`` schema."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from personal_assistant_sdk import HostDataClient


def content_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class EhallStore:
    def __init__(self, data: HostDataClient) -> None:
        self._data = data
        self._schema_ready = False

    async def migrate(self, migrations: Sequence[Mapping[str, Any]]) -> None:
        if self._schema_ready:
            return
        await self._data.migrate(migrations, timeout_seconds=60.0)
        self._schema_ready = True

    async def upsert_transaction(
        self,
        *,
        transaction_id: str,
        adapter_id: str,
        adapter_version: str,
        app_id: str,
        page_fingerprint: str,
        status: str,
        preview_hash: str | None = None,
    ) -> None:
        await self._data.execute(
            "INSERT INTO ehall_transactions (transaction_id, adapter_id, adapter_version, "
            "app_id, page_fingerprint, preview_hash, status, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, now()) "
            "ON CONFLICT (transaction_id) DO UPDATE SET "
            "adapter_id = EXCLUDED.adapter_id, adapter_version = EXCLUDED.adapter_version, "
            "app_id = EXCLUDED.app_id, page_fingerprint = EXCLUDED.page_fingerprint, "
            "preview_hash = COALESCE(EXCLUDED.preview_hash, ehall_transactions.preview_hash), "
            "status = EXCLUDED.status, updated_at = now()",
            [
                transaction_id,
                adapter_id,
                adapter_version,
                app_id,
                page_fingerprint,
                preview_hash,
                status,
            ],
        )

    async def record_action(
        self,
        *,
        action_id: str,
        transaction_id: str,
        kind: str,
        payload: Mapping[str, Any],
        status: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        await self._data.execute(
            "INSERT INTO ehall_actions (action_id, transaction_id, kind, payload_digest, "
            "status, detail) VALUES ($1, $2, $3, $4, $5, $6::jsonb) "
            "ON CONFLICT (action_id) DO NOTHING",
            [
                action_id,
                transaction_id,
                kind,
                content_digest(payload),
                status,
                json.dumps(dict(detail), ensure_ascii=False, sort_keys=True) if detail else None,
            ],
        )

    async def recent_transactions(self, limit: int = 10) -> tuple[Mapping[str, Any], ...]:
        response = await self._data.execute(
            "SELECT transaction_id, adapter_id, adapter_version, app_id, page_fingerprint, "
            "status, updated_at FROM ehall_transactions ORDER BY updated_at DESC LIMIT $1",
            [max(1, min(limit, 50))],
        )
        rows = response.get("rows", [])
        if not isinstance(rows, Sequence):
            return ()
        return tuple(row for row in rows if isinstance(row, Mapping))


__all__ = ["EhallStore", "content_digest"]
