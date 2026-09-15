from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from personal_assistant.domain import InvalidStateTransitionError, TaskRun, TaskState, utc_now

_ALLOWED: dict[TaskState, frozenset[TaskState]] = {
    TaskState.CREATED: frozenset({TaskState.QUEUED, TaskState.CANCELLED}),
    TaskState.QUEUED: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.WAITING_USER,
            TaskState.WAITING_APPROVAL,
            TaskState.WAITING_RECONCILIATION,
            TaskState.PAUSED_SAFETY,
            TaskState.PAUSED_EXTENSION,
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.WAITING_USER: frozenset({TaskState.QUEUED, TaskState.CANCELLED}),
    TaskState.WAITING_APPROVAL: frozenset(
        {TaskState.QUEUED, TaskState.WAITING_USER, TaskState.CANCELLED}
    ),
    TaskState.WAITING_RECONCILIATION: frozenset(
        {TaskState.QUEUED, TaskState.WAITING_USER, TaskState.CANCELLED}
    ),
    TaskState.PAUSED_SAFETY: frozenset({TaskState.QUEUED, TaskState.CANCELLED}),
    TaskState.PAUSED_EXTENSION: frozenset({TaskState.QUEUED, TaskState.CANCELLED}),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


def transition_run(
    run: TaskRun,
    target: TaskState,
    *,
    reference: str | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> TaskRun:
    if target not in _ALLOWED[run.state]:
        raise InvalidStateTransitionError(run.state, target)
    return replace(
        run,
        state=target,
        waiting_reference=reference,
        pause_reason=reason,
        updated_at=now or utc_now(),
        version=run.version + 1,
    )


def resume_as_new_segment(run: TaskRun, *, now: datetime | None = None) -> TaskRun:
    if run.state not in {
        TaskState.PAUSED_SAFETY,
        TaskState.WAITING_USER,
        TaskState.WAITING_APPROVAL,
        TaskState.WAITING_RECONCILIATION,
        TaskState.PAUSED_EXTENSION,
    }:
        raise InvalidStateTransitionError(run.state, TaskState.QUEUED)
    current = now or utc_now()
    return replace(
        run,
        state=TaskState.QUEUED,
        version=run.version + 1,
        segment_number=run.segment_number + 1,
        active_started_at=current,
        updated_at=current,
        consecutive_errors=0,
        no_progress_rounds=0,
        same_call_without_progress=0,
        last_call_fingerprint=None,
        last_observation_fingerprint=None,
        waiting_reference=None,
        pause_reason=None,
    )

