"""Shared, bounded ordering rules for durable queue management."""

from __future__ import annotations

from datetime import datetime

from personal_assistant.core.jobs.queue import Job
from personal_assistant.domain import ConcurrentModificationError, ValidationError

TASK_JOB_KINDS = frozenset({"agent.start", "agent.resume", "agent.approval"})
MAX_REORDER_JOBS = 5000


def priority(job: Job) -> int:
    value = job.metadata.get("queue_priority", 0)
    return value if type(value) is int and 0 <= value <= MAX_REORDER_JOBS else 0


def order_key(job: Job) -> tuple[int, datetime, datetime, str]:
    return (-priority(job), job.available_at, job.created_at, job.id)


def task_job(job: Job) -> bool:
    return (
        job.kind in TASK_JOB_KINDS
        and isinstance(job.payload.get("task_id"), str)
        and bool(job.payload["task_id"].strip())
    )


def validate_limit(limit: int) -> None:
    if not 1 <= limit <= 1000:
        raise ValidationError("queue page size must be between 1 and 1000")


def validate_move(direction: str, expected_version: int) -> None:
    if direction not in {"up", "down", "top", "bottom"} or expected_version < 0:
        raise ValidationError("invalid queue movement")


def reordered(jobs: list[Job], job_id: str, expected_version: int, direction: str) -> list[Job]:
    """Move only eligible task jobs, keeping every other job's relative order."""
    validate_move(direction, expected_version)
    candidates = [job for job in jobs if task_job(job)]
    selected = next((job for job in candidates if job.id == job_id), None)
    if selected is None or selected.version != expected_version:
        raise ConcurrentModificationError("queued job changed or is no longer ready")
    if len(jobs) > MAX_REORDER_JOBS:
        raise ValidationError("too many ready jobs to reorder safely")
    index = candidates.index(selected)
    target = {
        "up": max(0, index - 1),
        "down": min(len(candidates) - 1, index + 1),
        "top": 0,
        "bottom": len(candidates) - 1,
    }[direction]
    if index == target:
        return jobs
    ordered = list(jobs)
    destination = jobs.index(candidates[target])
    ordered.remove(selected)
    ordered.insert(destination, selected)
    return ordered
