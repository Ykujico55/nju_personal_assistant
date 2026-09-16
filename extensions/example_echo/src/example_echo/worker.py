"""Reference extension exercising tool, context, schedule and form slots."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_assistant_sdk import (
    PROTOCOL_VERSION,
    ContextQuery,
    DrainReport,
    Evidence,
    ExtensionInfo,
    FormSchemaDescriptor,
    HealthReport,
    InvocationContext,
    Outcome,
    RiskLevel,
    RuntimeContext,
    ScheduleDefinition,
    ScheduleMisfirePolicy,
    ToolDescriptor,
    ToolResult,
)
from personal_assistant_sdk.worker import run_stdio_worker


class EchoExtension:
    def __init__(self) -> None:
        self._runtime: RuntimeContext | None = None
        self._draining = False

    async def initialize(self, runtime: RuntimeContext) -> ExtensionInfo:
        self._runtime = runtime
        return ExtensionInfo(
            id=runtime.extension_id,
            version=runtime.extension_version,
            protocol_version=PROTOCOL_VERSION,
            slots=("ToolProvider", "ContextProvider", "ScheduleProvider", "FormSchemaProvider"),
            schema_hash=runtime.manifest_schema_hash,
        )

    async def health(self) -> HealthReport:
        return HealthReport(
            healthy=self._runtime is not None and not self._draining,
            status="draining" if self._draining else "ready",
        )

    async def drain(self, deadline: float) -> DrainReport:
        del deadline
        self._draining = True
        return DrainReport(drained=True, active_calls=0)

    async def shutdown(self) -> None:
        self._draining = True
        self._runtime = None

    def tools(self) -> tuple[ToolDescriptor, ...]:
        # The host compares these descriptors with the manifest and the schema
        # files byte-for-byte, so load the same files the manifest references.
        return (
            ToolDescriptor(
                id="example.echo",
                risk=RiskLevel.READ,
                description="Return exactly the supplied text.",
                input_schema=_load_schema("schemas/echo-input.json"),
                output_schema=_load_schema("schemas/echo-output.json"),
            ),
        )

    async def invoke(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        context: InvocationContext,
    ) -> ToolResult:
        if tool_id != "example.echo":
            raise ValueError(f"unknown tool: {tool_id}")
        text = arguments.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a non-empty string")
        prefix = ""
        if self._runtime is not None:
            configured = self._runtime.non_secret_config.get("prefix", "")
            prefix = configured if isinstance(configured, str) else ""
        return ToolResult(
            outcome=Outcome.SUCCEEDED,
            output={"echo": f"{prefix}{text}", "task_id": context.task_id},
        )

    async def retrieve(self, query: ContextQuery) -> tuple[Evidence, ...]:
        text = f"Echo extension context for: {query.text}"
        return (
            Evidence(
                text=text,
                source_id="example.echo_context",
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                source_uri="extension://example.echo/static-context",
                metadata={"observed_at": datetime.now(UTC).isoformat()},
            ),
        )

    def schedules(self) -> tuple[ScheduleDefinition, ...]:
        return (
            ScheduleDefinition(
                id="example.echo_hourly",
                timezone="Asia/Shanghai",
                misfire_policy=ScheduleMisfirePolicy.COALESCE,
                interval_seconds=3600,
            ),
        )

    def forms(self) -> tuple[FormSchemaDescriptor, ...]:
        return (
            FormSchemaDescriptor(
                id="example.echo_settings",
                json_schema={
                    "type": "object",
                    "properties": {"prefix": {"type": "string", "maxLength": 40}},
                    "additionalProperties": False,
                },
                ui_schema={"prefix": {"ui:placeholder": "Echo: "}},
                field_sensitivity={"prefix": "PUBLIC"},
            ),
        )


def _load_schema(relative: str) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    schema: dict[str, Any] = json.loads((root / relative).read_text("utf-8"))
    return schema


def create_extension() -> EchoExtension:
    return EchoExtension()


if __name__ == "__main__":
    run_stdio_worker(create_extension)

