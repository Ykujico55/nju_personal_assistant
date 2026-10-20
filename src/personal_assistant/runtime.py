"""Composition root for the independent durable worker; no business ids in core."""

from __future__ import annotations

from typing import Any

from personal_assistant.bootstrap import Container
from personal_assistant.core.agent.planner import StructuredPlanner
from personal_assistant.core.agent.runtime import TaskRuntime
from personal_assistant.core.event_delivery import EventCollector
from personal_assistant.core.tools.gateway import DefinitiveToolFailure
from personal_assistant.domain import ToolCall, ValidationError
from personal_assistant.infrastructure.mail.executor import MAIL_SEND_CAPABILITY
from personal_assistant.infrastructure.operations.control import HostControl


def build_runtime(container: Container) -> TaskRuntime:
    settings = container.settings
    provider = (
        settings.model_local_provider_id
        if settings.model_local_base_url
        else settings.model_remote_provider_id
    )
    extensions = container.extension_registry.snapshot.extensions
    grants = {}
    for descriptor in container.tool_registry.snapshot().descriptors():
        record = extensions.get(descriptor.extension_id)
        approved = set(record.manifest.capabilities.required) if record else set()
        if (
            record
            and settings.mail_send_enabled
            and MAIL_SEND_CAPABILITY in record.manifest.capabilities.optional
        ):
            approved.add(MAIL_SEND_CAPABILITY)
        grants[descriptor.id] = frozenset(approved)

    async def prepare(call: ToolCall) -> ToolCall:
        descriptor = container.tool_registry.snapshot().resolve(call.tool_id, call.tool_version)
        if MAIL_SEND_CAPABILITY in descriptor.required_capabilities:
            preview = container.tool_registry.snapshot().resolve(
                call.tool_id + ".preview", call.tool_version
            )
            try:
                return await container.mail_send_executor.prepare_call(
                    descriptor,
                    preview,
                    call.arguments,
                    task_id=call.task_id,
                    granted_capabilities=call.granted_capabilities,
                    workflow_allowed_tools=call.workflow_allowed_tools,
                )
            except DefinitiveToolFailure:
                raise ValidationError("authoritative mail materials are unavailable") from None
        raise ValidationError("a complete host-bound authority preview is required")

    async def sink(event: dict[str, Any], identity: str) -> None:
        for binding in await container.records.list("event-bindings"):
            value = binding["value"]
            if (
                not value.get("enabled")
                or value.get("event_type") != event["type"]
                or value.get("source_id") != event.get("source_id")
                or value.get("extension_id") != event.get("extension_id")
                or value.get("extension_version") != event.get("extension_version")
            ):
                continue
            task = await container.tasks.create(
                objective=value.get("objective", "处理新的来源事项"),
                idempotency_key="event-task:" + identity + ":" + binding["id"],
            )
            await build_runtime(container).request(
                task.id,
                version=task.version,
                mode="workflow",
                workflow=value["workflow"],
                inputs={**value.get("inputs", {}), "event": event["payload"]},
                key="event-workflow:" + identity + ":" + binding["id"],
            )

    collector = EventCollector(
        container.records, container.extension_supervisor.poll_source, sink=sink
    )

    async def poll_job(payload: dict[str, Any], job_id: str) -> dict[str, Any]:
        return await collector.collect(payload, job_id=job_id)

    return TaskRuntime(
        tasks=container.tasks,
        jobs=container.jobs,
        runs=container.run_repository,
        checkpoints=container.checkpoint_store,
        records=container.records,
        tools=container.tool_gateway,
        registry=container.tool_registry,
        planner=StructuredPlanner(
            container.model_router,
            provider,
            owner_id=getattr(getattr(container.storage, "config", None), "owner_id", "owner"),
            policy_identity={
                "provider_id": provider,
                "endpoint": settings.model_local_base_url or settings.model_remote_base_url,
                "model_id": settings.model_local_model or settings.model_remote_model,
            },
        ),
        approvals=container.approvals,
        grants=grants,
        prepare=prepare,
        paused_probe=HostControl(settings.extension_root.parent / "control").frozen,
        unit_of_work=getattr(container.storage, "database", None),
        poll_job=poll_job,
    )
