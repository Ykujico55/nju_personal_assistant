from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Literal

from fastapi import APIRouter, Depends, Response

from personal_assistant.api.dependencies import get_actor, get_container
from personal_assistant.api.errors import ApplicationError
from personal_assistant.bootstrap import Container
from personal_assistant.core.approvals import ApprovalExpiredError, ApprovalRecord
from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.core.tools.gateway import DefinitiveToolFailure
from personal_assistant.domain import ApprovalState, RiskLevel
from personal_assistant.domain.errors import NotFoundError, ValidationError

from .schemas import ApprovalDecision, ApprovalReviewView, ApprovalView, RejectDecision

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _blocked(
    kind: Literal["MAIL", "EHALL", "UNSUPPORTED"], reason: str
) -> ApprovalReviewView:
    return ApprovalReviewView(kind=kind, ready=False, reason=reason)


async def _review(record: ApprovalRecord, container: Container) -> ApprovalReviewView:
    action = record.action
    kind: Literal["MAIL", "EHALL", "UNSUPPORTED"] = (
        "EHALL" if action.get("tool_id") == "ehall.submit" else "UNSUPPORTED"
    )
    if record.state is not ApprovalState.WAITING_APPROVAL:
        return _blocked(kind, f"审批状态为 {record.state.value}，不能再次批准")
    try:
        fingerprint = canonical_sha256(action)
    except ValidationError:
        return _blocked(kind, "持久审批载荷无法验证")
    if not hmac.compare_digest(fingerprint, record.action_fingerprint):
        return _blocked(kind, "持久审批载荷摘要不匹配")
    identity_fields = (
        "action_type", "task_id", "tool_id", "tool_version", "extension_id",
        "extension_version",
    )
    if any(not isinstance(action.get(name), str) or not action[name] for name in identity_fields):
        return _blocked(kind, "持久审批身份不完整")
    if action["action_type"] != action["tool_id"]:
        return _blocked(kind, "审批动作与工具不一致")
    if not isinstance(action.get("payload"), Mapping) or not isinstance(
        action.get("target"), Mapping
    ):
        return _blocked(kind, "审批目标或载荷不完整")
    if not isinstance(action.get("attachments"), list):
        return _blocked(kind, "审批附件清单不完整")
    # F07's current trial preview omits the actual submit target, full fields,
    # materials, and consequences. It is never a public approvable receipt.
    if action["tool_id"] == "ehall.submit":
        return _blocked("EHALL", "F07 真实目标、完整字段、材料及后果尚未权威核验")
    await container.refresh_tool_registry()
    snapshot = container.tool_registry.snapshot()
    try:
        descriptor = snapshot.resolve(action["tool_id"], action["tool_version"])
    except NotFoundError:
        return _blocked(kind, "审批工具当前未启用或版本已改变")
    if (
        descriptor.extension_id != action["extension_id"]
        or descriptor.extension_version != action["extension_version"]
        or descriptor.risk is not RiskLevel.EXTERNAL_WRITE
    ):
        return _blocked(kind, "审批扩展或风险等级已改变")
    if "mail.send" not in descriptor.required_capabilities:
        return _blocked(kind, "此工具尚无可核对的手机审批预览")
    kind = "MAIL"
    target = action["target"]
    payload = action["payload"]
    if not isinstance(target.get("account_id"), str) or target["account_id"] != payload.get(
        "account_id"
    ):
        return _blocked(kind, "目标账户与邮件载荷不一致")
    try:
        preview_descriptor = snapshot.resolve(
            f"{descriptor.id}.preview", descriptor.version
        )
    except NotFoundError:
        return _blocked(kind, "只读邮件预览工具不可用")
    try:
        details = await container.mail_send_executor.review(
            descriptor, preview_descriptor, payload, task_id=action["task_id"]
        )
    except DefinitiveToolFailure:
        return _blocked(kind, "邮件正文或附件无法与审批快照核对")
    bound_attachments = action["attachments"]
    bound: list[tuple[str, str, int]] = []
    for item in bound_attachments:
        if not isinstance(item, Mapping):
            return _blocked(kind, "审批附件清单不完整")
        name, digest, size = item.get("name"), item.get("sha256"), item.get("size_bytes")
        if (
            not isinstance(name, str) or not isinstance(digest, str)
            or isinstance(size, bool) or not isinstance(size, int)
        ):
            return _blocked(kind, "审批附件清单不完整")
        bound.append((name, digest, size))
    shown = [
        (item["name"], item["sha256"], item["size_bytes"])
        for item in details["attachments"]
    ]
    if sorted(bound) != sorted(shown):
        return _blocked(kind, "审批附件哈希与实际邮件不一致")
    return ApprovalReviewView(kind=kind, ready=True, details=dict(details))


async def _view(record: ApprovalRecord, container: Container) -> ApprovalView:
    review = await _review(record, container)
    return ApprovalView(
        id=record.id,
        state=record.state.value,
        action=record.action,
        action_fingerprint=record.action_fingerprint,
        created_at=record.created_at,
        nonce=record.nonce if review.ready else None,
        expires_at=record.expires_at,
        version=record.version,
        review=review,
    )


@router.get("/{approval_id}", response_model=ApprovalView)
async def get_approval(
    approval_id: str,
    response: Response,
    container: Container = Depends(get_container),
) -> ApprovalView:
    response.headers["Cache-Control"] = "no-store"
    return await _view(await container.approvals.get(approval_id), container)


@router.post("/{approval_id}/approve", response_model=ApprovalView)
async def approve(
    approval_id: str,
    payload: ApprovalDecision,
    response: Response,
    container: Container = Depends(get_container),
    actor: str = Depends(get_actor),
) -> ApprovalView:
    response.headers["Cache-Control"] = "no-store"
    current = await container.approvals.get(approval_id)
    if current.state is ApprovalState.EXPIRED:
        raise ApprovalExpiredError(f"approval expired: {approval_id}")
    review = await _review(current, container)
    if not review.ready:
        raise ApplicationError(
            code="APPROVAL_REVIEW_UNAVAILABLE",
            message=review.reason or "approval review is unavailable",
            status_code=409,
        )
    record = await container.approvals.approve(
        approval_id,
        nonce=payload.nonce,
        actor_id=actor,
    )
    return await _view(record, container)


@router.post("/{approval_id}/reject", response_model=ApprovalView)
async def reject(
    approval_id: str,
    payload: RejectDecision,
    response: Response,
    container: Container = Depends(get_container),
) -> ApprovalView:
    response.headers["Cache-Control"] = "no-store"
    return await _view(
        await container.approvals.reject(approval_id, reason=payload.reason), container
    )
