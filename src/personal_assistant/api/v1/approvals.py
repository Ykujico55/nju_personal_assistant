from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from personal_assistant.api.dependencies import get_actor, get_container
from personal_assistant.bootstrap import Container
from personal_assistant.core.approvals import ApprovalRecord
from personal_assistant.domain import ApprovalState

from .schemas import ApprovalDecision, ApprovalView, RejectDecision

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _view(record: ApprovalRecord) -> ApprovalView:
    show_nonce = record.state is ApprovalState.WAITING_APPROVAL
    return ApprovalView(
        id=record.id,
        state=record.state.value,
        action=record.action,
        nonce=record.nonce if show_nonce else None,
        expires_at=record.expires_at,
        version=record.version,
    )


@router.get("/{approval_id}", response_model=ApprovalView)
async def get_approval(
    approval_id: str,
    response: Response,
    container: Container = Depends(get_container),
) -> ApprovalView:
    response.headers["Cache-Control"] = "no-store"
    return _view(await container.approvals.get(approval_id))


@router.post("/{approval_id}/approve", response_model=ApprovalView)
async def approve(
    approval_id: str,
    payload: ApprovalDecision,
    response: Response,
    container: Container = Depends(get_container),
    actor: str = Depends(get_actor),
) -> ApprovalView:
    response.headers["Cache-Control"] = "no-store"
    record = await container.approvals.approve(
        approval_id,
        nonce=payload.nonce,
        actor_id=actor,
    )
    return _view(record)


@router.post("/{approval_id}/reject", response_model=ApprovalView)
async def reject(
    approval_id: str,
    payload: RejectDecision,
    response: Response,
    container: Container = Depends(get_container),
) -> ApprovalView:
    response.headers["Cache-Control"] = "no-store"
    return _view(await container.approvals.reject(approval_id, reason=payload.reason))
