"""Development-only Web Push subscription store."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from personal_assistant.core.notifications import PushSubscriptionReceipt, PushSubscriptionRecord
from personal_assistant.domain import ConcurrentModificationError


class InMemoryPushSubscriptionStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], PushSubscriptionRecord] = {}
        self._commands: dict[tuple[str, str], tuple[str, PushSubscriptionReceipt]] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: PushSubscriptionRecord) -> PushSubscriptionRecord:
        async with self._lock:
            key = (record.owner_id, record.id)
            previous = self._records.get(key)
            saved = replace(record, created_at=previous.created_at) if previous else record
            self._records[key] = saved
            return saved

    async def replay(
        self, owner_id: str, key: str, request_sha256: str
    ) -> PushSubscriptionReceipt | None:
        async with self._lock:
            command = self._commands.get((owner_id, key))
            if command is None:
                return None
            if command[0] != request_sha256:
                raise ConcurrentModificationError(
                    "push idempotency key was reused with new content"
                )
            return command[1]

    async def put_command(
        self, record: PushSubscriptionRecord, key: str, request_sha256: str
    ) -> PushSubscriptionReceipt:
        async with self._lock:
            command = self._commands.get((record.owner_id, key))
            if command is not None:
                if command[0] != request_sha256:
                    raise ConcurrentModificationError(
                        "push idempotency key was reused with new content"
                    )
                return command[1]
            record_key = (record.owner_id, record.id)
            previous = self._records.get(record_key)
            saved = replace(record, created_at=previous.created_at) if previous else record
            receipt = PushSubscriptionReceipt(id=saved.id, created_at=saved.created_at)
            self._records[record_key] = saved
            self._commands[(record.owner_id, key)] = (request_sha256, receipt)
            return receipt

    async def get(self, owner_id: str, subscription_id: str) -> PushSubscriptionRecord | None:
        async with self._lock:
            return self._records.get((owner_id, subscription_id))

    async def list_active(self, owner_id: str) -> tuple[PushSubscriptionRecord, ...]:
        async with self._lock:
            return tuple(
                record for (owner, _), record in self._records.items() if owner == owner_id
            )

    async def revoke(self, owner_id: str, subscription_id: str) -> None:
        async with self._lock:
            self._records.pop((owner_id, subscription_id), None)
