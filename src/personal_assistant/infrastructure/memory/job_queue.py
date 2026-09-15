"""Reference queue implementation; never use it as production persistence."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from personal_assistant.core.jobs.queue import Job, JobState, LeaseConflict


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InMemoryJobQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        *,
        kind: str,
        payload: dict[str, object],
        idempotency_key: str,
        available_at: datetime | None = None,
        max_attempts: int = 5,
    ) -> Job:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        async with self._lock:
            existing_id = self._idempotency.get((kind, idempotency_key))
            if existing_id is not None:
                existing = self._jobs[existing_id]
                if existing.payload != dict(payload):
                    raise ValueError(
                        "idempotency key was reused with a different job payload"
                    )
                return deepcopy(existing)
            now = _utcnow()
            job = Job(
                id=f"job_{uuid4().hex}",
                kind=kind,
                payload=dict(payload),
                idempotency_key=idempotency_key,
                created_at=now,
                available_at=available_at or now,
                max_attempts=max_attempts,
            )
            self._jobs[job.id] = job
            self._idempotency[(kind, idempotency_key)] = job.id
            return deepcopy(job)

    async def claim(self, *, worker_id: str, lease_seconds: int = 30) -> Job | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        async with self._lock:
            now = _utcnow()
            candidates: list[Job] = []
            for job in self._jobs.values():
                expired = (
                    job.state is JobState.LEASED
                    and job.lease_until is not None
                    and job.lease_until <= now
                )
                if expired:
                    job.state = JobState.READY
                    job.lease_owner = None
                    job.lease_until = None
                if job.state is JobState.READY and job.available_at <= now:
                    candidates.append(job)
            if not candidates:
                return None
            job = min(candidates, key=lambda item: (item.available_at, item.created_at, item.id))
            job.state = JobState.LEASED
            job.lease_owner = worker_id
            job.lease_until = now + timedelta(seconds=lease_seconds)
            job.attempts += 1
            job.version += 1
            return deepcopy(job)

    async def heartbeat(
        self, *, job_id: str, worker_id: str, lease_seconds: int = 30
    ) -> Job:
        async with self._lock:
            job = self._require_lease(job_id, worker_id)
            job.lease_until = _utcnow() + timedelta(seconds=lease_seconds)
            job.version += 1
            return deepcopy(job)

    async def complete(
        self, *, job_id: str, worker_id: str, result: dict[str, object]
    ) -> Job:
        async with self._lock:
            job = self._require_lease(job_id, worker_id)
            job.state = JobState.SUCCEEDED
            job.result = dict(result)
            self._release(job)
            return deepcopy(job)

    async def release(self, *, job_id: str, worker_id: str) -> Job:
        async with self._lock:
            job = self._require_lease(job_id, worker_id)
            job.state = JobState.READY
            job.available_at = _utcnow()
            self._release(job)
            return deepcopy(job)

    async def fail(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        retryable: bool,
        retry_at: datetime | None = None,
    ) -> Job:
        async with self._lock:
            job = self._require_lease(job_id, worker_id)
            job.error_code = error_code
            if retryable and job.attempts < job.max_attempts:
                job.state = JobState.READY
                job.available_at = retry_at or _utcnow()
            else:
                job.state = JobState.DEAD_LETTER if retryable else JobState.FAILED
            self._release(job)
            return deepcopy(job)

    async def mark_unknown(
        self, *, job_id: str, worker_id: str, diagnostic_code: str
    ) -> Job:
        async with self._lock:
            job = self._require_lease(job_id, worker_id)
            job.state = JobState.WAITING_RECONCILIATION
            job.error_code = diagnostic_code
            self._release(job)
            return deepcopy(job)

    async def get(self, job_id: str) -> Job | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job is not None else None

    def _require_lease(self, job_id: str, worker_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if (
            job.state is not JobState.LEASED
            or job.lease_owner != worker_id
            or job.lease_until is None
            or job.lease_until <= _utcnow()
        ):
            raise LeaseConflict(f"worker {worker_id!r} does not own job {job_id!r}")
        return job

    @staticmethod
    def _release(job: Job) -> None:
        job.lease_owner = None
        job.lease_until = None
        job.version += 1
