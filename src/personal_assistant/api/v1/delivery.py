"""Single-owner public task operation, declarations and rule review surfaces."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from personal_assistant.api.dependencies import get_actor, get_container
from personal_assistant.bootstrap import Container
from personal_assistant.core.governance import RuleService
from personal_assistant.core.models import ContextField, DataClassification, ModelRequest
from personal_assistant.core.workflows import validate_workflow
from personal_assistant.domain import ConcurrentModificationError, NotFoundError, ValidationError
from personal_assistant.runtime import build_runtime

from .disclosures import _consent_view, _preview_view

router = APIRouter(tags=["assistant"])


class ExecutionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0, strict=True)
    mode: Literal["agent", "workflow"] = "agent"
    workflow: dict[str, Any] | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    consent_id: str | None = Field(default=None, max_length=256)


class RuleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: dict[str, Any]
    preferences: dict[str, Any]


class RuleMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, strict=True)
    tests: list[dict[str, Any]] = Field(default_factory=list, max_length=32)


@router.get("/system")
async def system(container: Container = Depends(get_container)) -> dict[str, Any]:
    heartbeat = await container.records.get("hosts", "agent")
    online = False
    if heartbeat:
        seen = datetime.fromisoformat(heartbeat["value"]["seen_at"])
        online = datetime.now(UTC) - seen < timedelta(seconds=45)
    settings = container.settings
    return {
        "model_configured": container.model_router is not None,
        "worker_online": online,
        "storage": settings.storage_backend,
        "notification_channel": "ntfy" if settings.ntfy_publish_url else "web_push",
        "notification_configured": bool(
            settings.ntfy_publish_url or settings.push_vapid_public_key
        ),
        "frozen": container.host_control.frozen(),
        "mail_send_enabled": settings.mail_send_enabled,
        "browser_submit_enabled": settings.browser_submit_enabled,
        "external_configuration_pending": container.model_router is None,
    }


@router.get("/workflows")
async def workflows(container: Container = Depends(get_container)) -> dict[str, Any]:
    items = []
    for item in await container.extension_supervisor.list_declarations("workflow.list"):
        try:
            items.append({**validate_workflow(item), "supported": True})
        except ValidationError:
            items.append(
                {
                    "id": item.get("id"),
                    "version": item.get("version"),
                    "extension_id": item.get("extension_id"),
                    "supported": False,
                    "reason": "工作流使用了不支持的声明能力，已禁用",
                }
            )
    return {"items": items}


@router.get("/tasks/{task_id}/execution")
async def execution(task_id: str, container: Container = Depends(get_container)) -> dict[str, Any]:
    task = await container.tasks.get(task_id)
    record = await container.records.get("executions", task_id)
    checkpoint = await container.checkpoint_store.load("run_" + task_id)
    return {
        "task_id": task_id,
        "task_version": task.version,
        "state": task.state.value,
        "execution": record["value"] if record else None,
        "pending_input": checkpoint.get("pending_input"),
        "workflow": checkpoint.get("workflow"),
        "model_disclosure": {"required": True} if checkpoint.get("model_disclosure") else None,
    }


@router.post("/tasks/{task_id}/execution", status_code=202)
async def request_execution(
    task_id: str,
    payload: ExecutionCommand,
    request: Request,
    container: Container = Depends(get_container),
    actor: str = Depends(get_actor),
) -> dict[str, Any]:
    # The command fingerprint is the original request, before reading the current catalog.
    async def apply() -> dict[str, Any]:
        definition = None
        if payload.mode == "workflow":
            reference = payload.workflow or {}
            declarations = await container.extension_supervisor.list_declarations("workflow.list")
            definition = next(
                (
                    d
                    for d in declarations
                    if d.get("id") == reference.get("id")
                    and d.get("version") == reference.get("version")
                    and d.get("extension_id") == reference.get("extension_id")
                    and d.get("extension_version") == reference.get("extension_version")
                ),
                None,
            )
            if definition is None:
                raise NotFoundError("workflow version is not available")
        receipt = await build_runtime(container).request(
            task_id,
            version=payload.version,
            mode=payload.mode,
            key="runtime:" + request.state.idempotency_key,
            workflow=definition,
            inputs=payload.inputs,
            consent_id=payload.consent_id,
            user_id=actor,
        )
        return {**receipt, "command": payload.model_dump()}

    return await container.records.command(
        "public-execution:" + request.state.idempotency_key,
        {"task_id": task_id, **payload.model_dump()},
        apply,
    )


def rule_service(container: Container) -> RuleService:
    return RuleService(
        container.records,
        history=container.rule_history,
    )


@router.get("/rules")
async def rules(container: Container = Depends(get_container)) -> dict[str, Any]:
    return {"items": await container.records.list("rules")}


@router.post("/rules", status_code=201)
async def propose_rule(
    payload: RuleProposal, request: Request, container: Container = Depends(get_container)
) -> dict[str, Any]:
    return await rule_service(container).propose(
        scope=payload.scope,
        preferences=payload.preferences,
        key="rule:" + request.state.idempotency_key,
    )


@router.post("/rules/{rule_id}/activate")
async def activate_rule(
    rule_id: str,
    payload: RuleMutation,
    request: Request,
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    return await rule_service(container).activate(
        rule_id,
        version=payload.version,
        tests=payload.tests,
        key="rule:" + request.state.idempotency_key,
    )


@router.post("/rules/{rule_id}/disable")
async def disable_rule(
    rule_id: str,
    payload: RuleMutation,
    request: Request,
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    return await rule_service(container).disable(
        rule_id, version=payload.version, key="rule:" + request.state.idempotency_key
    )


@router.get("/schedules")
async def schedules(container: Container = Depends(get_container)) -> dict[str, Any]:
    return {"items": await container.records.list("schedules")}


class TaskDisclosureConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0, strict=True)
    preview_hash: str = Field(min_length=64, max_length=64)


async def task_model_request(container: Container, task_id: str) -> tuple[str, ModelRequest]:
    await container.tasks.get(task_id)
    checkpoint = await container.checkpoint_store.load("run_" + task_id)
    value = checkpoint.get("model_disclosure")
    if not value:
        raise NotFoundError("this task has no pending model disclosure")
    return value["provider_id"], ModelRequest(
        purpose=value["purpose"],
        instruction=value["instruction"],
        fields=tuple(
            ContextField(
                f["name"], f["value"], DataClassification(f["classification"]), f["source"]
            )
            for f in value["fields"]
        ),
    )


@router.post("/tasks/{task_id}/model-disclosure/preview")
async def preview_task_disclosure(
    task_id: str, container: Container = Depends(get_container)
) -> dict[str, Any]:
    provider, model_request = await task_model_request(container, task_id)
    preview = container.disclosures.preview(model_request, provider_id=provider)
    task = await container.tasks.get(task_id)
    return {**_preview_view(preview).model_dump(mode="json"), "task_version": task.version}


@router.post("/tasks/{task_id}/model-disclosure", status_code=202)
async def confirm_task_disclosure(
    task_id: str,
    payload: TaskDisclosureConfirmation,
    request: Request,
    container: Container = Depends(get_container),
    actor: str = Depends(get_actor),
) -> dict[str, Any]:
    async def apply() -> dict[str, Any]:
        task = await container.tasks.get(task_id)
        if task.version != payload.version:
            raise ConcurrentModificationError("task changed; review the new disclosure")
        provider, model_request = await task_model_request(container, task_id)
        record = await container.disclosures.confirm(
            model_request,
            provider_id=provider,
            user_id=actor,
            preview_hash=payload.preview_hash,
            idempotency_key="task-model:" + request.state.idempotency_key,
        )
        return _consent_view(record).model_dump(mode="json")

    return await container.records.command(
        "task-model:" + request.state.idempotency_key,
        {"task_id": task_id, "actor": actor, **payload.model_dump()},
        apply,
    )


class RuleRollback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, strict=True)
    revision: str = Field(pattern=r"^[0-9a-f]{40,64}$")


@router.get("/rules/{rule_id}/history")
async def rule_history(
    rule_id: str, container: Container = Depends(get_container)
) -> dict[str, Any]:
    return {
        "versions": await container.rule_history.versions(rule_id)
    }


@router.post("/rules/{rule_id}/rollback")
async def rollback_rule(
    rule_id: str,
    payload: RuleRollback,
    request: Request,
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    return await rule_service(container).rollback(
        rule_id,
        revision=payload.revision,
        version=payload.version,
        key="rule:" + request.state.idempotency_key,
    )
