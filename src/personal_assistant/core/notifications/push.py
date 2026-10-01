"""Pure Web Push state and storage contracts; crypto belongs to infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class PushUnavailableError(RuntimeError):
    """Push configuration or host credential material is unavailable."""


@dataclass(frozen=True, slots=True)
class PushSubscriptionRecord:
    id: str
    owner_id: str
    sealed: bytes
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PushSubscriptionReceipt:
    id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PushSubscriptionHealth:
    active: bool
    reconfigure_required: bool


@dataclass(frozen=True, slots=True)
class PushDeliveryReport:
    accepted: int = 0
    expired: int = 0
    unknown: int = 0
    reconfigure_required: int = 0


class PushSubscriptionStore(Protocol):
    async def put(self, record: PushSubscriptionRecord) -> PushSubscriptionRecord: ...
    async def replay(
        self, owner_id: str, key: str, request_sha256: str
    ) -> PushSubscriptionReceipt | None: ...
    async def put_command(
        self, record: PushSubscriptionRecord, key: str, request_sha256: str
    ) -> PushSubscriptionReceipt: ...
    async def get(self, owner_id: str, subscription_id: str) -> PushSubscriptionRecord | None: ...
    async def list_active(self, owner_id: str) -> tuple[PushSubscriptionRecord, ...]: ...
    async def revoke(self, owner_id: str, subscription_id: str) -> None: ...
