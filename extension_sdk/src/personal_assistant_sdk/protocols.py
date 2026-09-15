"""Structural protocols implemented by extension workers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .models import (
    ContextQuery,
    DeliveryReceipt,
    DrainReport,
    EventSourceDescriptor,
    Evidence,
    ExtensionInfo,
    FormSchemaDescriptor,
    HealthReport,
    InvocationContext,
    JsonValue,
    MigrationDescriptor,
    NotificationRequest,
    PollRequest,
    PollResult,
    RuntimeContext,
    ScheduleDefinition,
    ToolDescriptor,
    ToolResult,
    WorkflowDefinition,
)


@runtime_checkable
class Extension(Protocol):
    async def initialize(self, runtime: RuntimeContext) -> ExtensionInfo: ...

    async def health(self) -> HealthReport: ...

    async def drain(self, deadline: float) -> DrainReport: ...

    async def shutdown(self) -> None: ...


@runtime_checkable
class ToolProvider(Protocol):
    def tools(self) -> Sequence[ToolDescriptor]: ...

    async def invoke(
        self,
        tool_id: str,
        arguments: dict[str, JsonValue],
        context: InvocationContext,
    ) -> ToolResult: ...


@runtime_checkable
class ContextProvider(Protocol):
    async def retrieve(self, query: ContextQuery) -> Sequence[Evidence]: ...


@runtime_checkable
class EventSource(Protocol):
    def event_sources(self) -> Sequence[EventSourceDescriptor]: ...

    async def poll(self, request: PollRequest) -> PollResult: ...


@runtime_checkable
class WorkflowProvider(Protocol):
    def workflows(self) -> Sequence[WorkflowDefinition]: ...


@runtime_checkable
class ScheduleProvider(Protocol):
    def schedules(self) -> Sequence[ScheduleDefinition]: ...


@runtime_checkable
class NotificationProvider(Protocol):
    async def deliver(self, request: NotificationRequest) -> DeliveryReceipt: ...


@runtime_checkable
class MigrationProvider(Protocol):
    def migrations(self) -> Sequence[MigrationDescriptor]: ...


@runtime_checkable
class FormSchemaProvider(Protocol):
    def forms(self) -> Sequence[FormSchemaDescriptor]: ...
