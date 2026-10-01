"""PostgreSQL Web Push subscriptions; only sealed capability bytes are stored."""

from __future__ import annotations

from personal_assistant.core.notifications import PushSubscriptionReceipt, PushSubscriptionRecord
from personal_assistant.domain import ConcurrentModificationError

from ._support import aware_utc
from .connection import PostgresDatabase


class PostgresPushSubscriptionStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def put(self, record: PushSubscriptionRecord) -> PushSubscriptionRecord:
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO push_subscriptions
                    (owner_id, id, sealed, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $4)
                ON CONFLICT (owner_id, id) DO UPDATE
                    SET sealed = EXCLUDED.sealed, updated_at = EXCLUDED.updated_at
                RETURNING created_at
                """,
                record.owner_id, record.id, record.sealed, record.created_at,
            )
        assert row is not None
        return PushSubscriptionRecord(
            id=record.id, owner_id=record.owner_id, sealed=record.sealed,
            created_at=aware_utc(row["created_at"]),
        )

    async def replay(
        self, owner_id: str, key: str, request_sha256: str
    ) -> PushSubscriptionReceipt | None:
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                """SELECT request_sha256, subscription_id, created_at
                   FROM push_subscription_commands
                   WHERE owner_id=$1 AND idempotency_key=$2""",
                owner_id, key,
            )
        if row is None:
            return None
        if row["request_sha256"] != request_sha256:
            raise ConcurrentModificationError("push idempotency key was reused with new content")
        return PushSubscriptionReceipt(
            id=row["subscription_id"], created_at=aware_utc(row["created_at"])
        )

    async def put_command(
        self, record: PushSubscriptionRecord, key: str, request_sha256: str
    ) -> PushSubscriptionReceipt:
        async with self._database.transaction(), self._database.connection() as connection:
            claimed = await connection.fetchval(
                """INSERT INTO push_subscription_commands
                       (owner_id, idempotency_key, request_sha256,
                        subscription_id, created_at)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (owner_id, idempotency_key) DO NOTHING
                   RETURNING idempotency_key""",
                record.owner_id, key, request_sha256, record.id, record.created_at,
            )
            if claimed is None:
                replayed = await self.replay(record.owner_id, key, request_sha256)
                assert replayed is not None
                return replayed
            saved = await self.put(record)
            await connection.execute(
                """UPDATE push_subscription_commands SET created_at=$3
                   WHERE owner_id=$1 AND idempotency_key=$2""",
                record.owner_id, key, saved.created_at,
            )
            return PushSubscriptionReceipt(id=saved.id, created_at=saved.created_at)

    async def get(self, owner_id: str, subscription_id: str) -> PushSubscriptionRecord | None:
        async with self._database.connection() as connection:
            row = await connection.fetchrow(
                "SELECT sealed, created_at FROM push_subscriptions WHERE owner_id=$1 AND id=$2",
                owner_id, subscription_id,
            )
        if row is None:
            return None
        return PushSubscriptionRecord(
            id=subscription_id, owner_id=owner_id, sealed=bytes(row["sealed"]),
            created_at=aware_utc(row["created_at"]),
        )

    async def list_active(self, owner_id: str) -> tuple[PushSubscriptionRecord, ...]:
        async with self._database.connection() as connection:
            rows = await connection.fetch(
                "SELECT id, sealed, created_at FROM push_subscriptions WHERE owner_id=$1",
                owner_id,
            )
        return tuple(
            PushSubscriptionRecord(
                id=row["id"], owner_id=owner_id, sealed=bytes(row["sealed"]),
                created_at=aware_utc(row["created_at"]),
            )
            for row in rows
        )

    async def revoke(self, owner_id: str, subscription_id: str) -> None:
        async with self._database.connection() as connection:
            await connection.execute(
                "DELETE FROM push_subscriptions WHERE owner_id=$1 AND id=$2",
                owner_id, subscription_id,
            )
