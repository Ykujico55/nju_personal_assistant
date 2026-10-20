"""Generic, local-only host controls and explicit declaration bindings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from personal_assistant.api.dependencies import get_container
from personal_assistant.bootstrap import Container
from personal_assistant.core.scheduling import DurableScheduler
from personal_assistant.core.workflows import validate_workflow
from personal_assistant.domain import ConcurrentModificationError, ValidationError

router = APIRouter(prefix="/admin/v1", tags=["local-operations"])


class ScheduleBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    extension_id: str = Field(min_length=1, max_length=128)
    extension_version: str = Field(min_length=1, max_length=128)


class VersionMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, strict=True)
    enabled: bool = Field(strict=True)


class EventBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    version: int = Field(default=0, ge=0, strict=True)
    extension_id: str
    extension_version: str
    source_id: str
    event_type: str
    workflow: dict[str, str]
    objective: str = Field(min_length=1, max_length=1000)
    inputs: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = Field(default=False, strict=True)


@router.get("/host")
async def status(container: Container = Depends(get_container)) -> dict[str, Any]:
    pending = await container.records.list("executions")
    return {
        "frozen": container.host_control.frozen(),
        "unconfirmed_executions": [
            {"task_id": r["id"], "reference": r["value"].get("reference")}
            for r in pending
            if r["value"].get("state") == "WAITING_RECONCILIATION"
        ],
        "storage": container.settings.storage_backend,
    }


@router.post("/host/freeze")
async def freeze(container: Container = Depends(get_container)) -> dict[str, bool]:
    container.host_control.freeze()
    return {"frozen": True}


@router.post("/host/resume")
async def resume(container: Container = Depends(get_container)) -> dict[str, bool]:
    container.host_control.resume()
    return {"frozen": False}


@router.get("/declarations")
async def declarations(container: Container = Depends(get_container)) -> dict[str, Any]:
    return {
        "schedules": await container.extension_supervisor.list_declarations("schedule.list"),
        "event_sources": await container.extension_supervisor.list_declarations(
            "event_source.list"
        ),
        "workflows": await container.extension_supervisor.list_declarations("workflow.list"),
    }


@router.get("/schedules")
async def schedules(container: Container = Depends(get_container)) -> dict[str, Any]:
    return {"items": await container.records.list("schedules")}


@router.post("/schedules", status_code=201)
async def bind_schedule(
    payload: ScheduleBinding, request: Request, container: Container = Depends(get_container)
) -> dict[str, Any]:
    async def apply() -> dict[str, Any]:
        schedules = await container.extension_supervisor.list_declarations("schedule.list")
        sources = await container.extension_supervisor.list_declarations("event_source.list")
        scheduled = next(
            (
                s
                for s in schedules
                if s.get("id") == payload.schedule_id
                and s.get("extension_id") == payload.extension_id
                and s.get("extension_version") == payload.extension_version
            ),
            None,
        )
        source = next(
            (
                s
                for s in sources
                if s.get("id") == payload.source_id
                and s.get("extension_id") == payload.extension_id
                and s.get("extension_version") == payload.extension_version
            ),
            None,
        )
        if scheduled is None or source is None:
            raise ValidationError("schedule must bind the currently published source and version")
        definition = {
            "id": payload.schedule_id,
            "source_id": payload.source_id,
            "extension_id": payload.extension_id,
            "extension_version": payload.extension_version,
            "timezone": scheduled["timezone"],
            "misfire_policy": scheduled["misfire_policy"],
            "max_catch_up": 1,
        }
        for key in ["cron", "interval_seconds"]:
            if scheduled.get(key) is not None:
                definition[key] = scheduled[key]
        return await DurableScheduler(container.records, container.jobs).configure(
            definition, key="scheduled:" + request.state.idempotency_key
        )

    return await container.records.command(
        "bind-schedule:" + request.state.idempotency_key, payload.model_dump(), apply
    )


@router.post("/schedules/{schedule_id}")
async def schedule_state(
    schedule_id: str,
    payload: VersionMutation,
    request: Request,
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    async def apply() -> dict[str, Any]:
        old = await container.records.get("schedules", schedule_id)
        if old is None:
            raise ValidationError("schedule is not configured")
        return await container.records.put(
            "schedules",
            schedule_id,
            {**old["value"], "enabled": payload.enabled},
            expected_version=payload.version,
        )

    return await container.records.command(
        "schedule-state:" + request.state.idempotency_key,
        {"id": schedule_id, **payload.model_dump()},
        apply,
    )


@router.get("/event-bindings")
async def event_bindings(container: Container = Depends(get_container)) -> dict[str, Any]:
    return {"items": await container.records.list("event-bindings")}


@router.post("/event-bindings")
async def bind_event(
    payload: EventBinding, request: Request, container: Container = Depends(get_container)
) -> dict[str, Any]:
    async def apply() -> dict[str, Any]:
        sources = await container.extension_supervisor.list_declarations("event_source.list")
        source = next(
            (
                s
                for s in sources
                if s.get("id") == payload.source_id
                and s.get("extension_id") == payload.extension_id
                and s.get("extension_version") == payload.extension_version
            ),
            None,
        )
        if source is None or payload.event_type not in source.get("event_types", []):
            raise ValidationError("event type is not declared by the selected source/version")
        flows = await container.extension_supervisor.list_declarations("workflow.list")
        flow = next(
            (
                f
                for f in flows
                if all(
                    f.get(k) == payload.workflow.get(k)
                    for k in ["id", "version", "extension_id", "extension_version"]
                )
            ),
            None,
        )
        if flow is None:
            raise ValidationError("select an exact published workflow version")
        old = await container.records.get("event-bindings", payload.id)
        if (old["version"] if old else 0) != payload.version:
            raise ConcurrentModificationError("binding changed; review its current version")
        value = {**payload.model_dump(), "workflow": validate_workflow(flow)}
        return await container.records.put(
            "event-bindings", payload.id, value, expected_version=payload.version
        )

    return await container.records.command(
        "event-binding:" + request.state.idempotency_key, payload.model_dump(), apply
    )
