"""Queue contracts shared by workers and persistence adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class JobState(StrEnum):
    READY = "READY"
    LEASED = "LEASED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    WAITING_RECONCILIATION = "WAITING_RECONCILIATION"


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    idempotency_key: str
    created_at: datetime
    available_at: datetime
    state: JobState = JobState.READY
    attempts: int = 0
    max_attempts: int = 5
    lease_owner: str | None = None
    lease_until: datetime | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    version: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class LeaseConflict(RuntimeError):
    """The caller no longer owns the active lease."""


class JobQueuePort(Protocol):
    async def enqueue(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
        available_at: datetime | None = None,
        max_attempts: int = 5,
    ) -> Job: ...

    async def claim(self, *, worker_id: str, lease_seconds: int = 30) -> Job | None: ...

    async def heartbeat(
        self, *, job_id: str, worker_id: str, lease_seconds: int = 30
    ) -> Job: ...

    async def release(self, *, job_id: str, worker_id: str) -> Job: ...

    async def complete(
        self, *, job_id: str, worker_id: str, result: dict[str, Any]
    ) -> Job: ...

    async def fail(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        retryable: bool,
        retry_at: datetime | None = None,
    ) -> Job: ...

    async def mark_unknown(
        self, *, job_id: str, worker_id: str, diagnostic_code: str
    ) -> Job: ...

    async def get(self, job_id: str) -> Job | None: ...
