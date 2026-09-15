"""Approval/action state transition policy."""

from __future__ import annotations

from personal_assistant.domain.enums import ApprovalState
from personal_assistant.domain.errors import InvalidStateTransitionError

_ALLOWED: dict[ApprovalState, frozenset[ApprovalState]] = {
    ApprovalState.DRAFT: frozenset({ApprovalState.PREPARED, ApprovalState.CANCELLED}),
    ApprovalState.PREPARED: frozenset(
        {ApprovalState.WAITING_APPROVAL, ApprovalState.CANCELLED}
    ),
    ApprovalState.WAITING_APPROVAL: frozenset(
        {ApprovalState.APPROVED, ApprovalState.EXPIRED, ApprovalState.CANCELLED}
    ),
    ApprovalState.APPROVED: frozenset(
        {ApprovalState.EXECUTING, ApprovalState.EXPIRED, ApprovalState.CANCELLED}
    ),
    ApprovalState.EXECUTING: frozenset(
        {
            ApprovalState.SUCCEEDED,
            ApprovalState.FAILED,
            ApprovalState.UNKNOWN,
        }
    ),
    ApprovalState.SUCCEEDED: frozenset(),
    ApprovalState.FAILED: frozenset(),
    ApprovalState.UNKNOWN: frozenset(),
    ApprovalState.EXPIRED: frozenset(),
    ApprovalState.CANCELLED: frozenset(),
}


def ensure_approval_transition(
    current: ApprovalState, target: ApprovalState
) -> None:
    if target not in _ALLOWED[current]:
        raise InvalidStateTransitionError(current, target)


def allowed_approval_transitions(state: ApprovalState) -> frozenset[ApprovalState]:
    return _ALLOWED[state]
