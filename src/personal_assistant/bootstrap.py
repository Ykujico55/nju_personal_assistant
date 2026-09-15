"""Composition root. Business logic must not be added here."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.extensions import ExtensionRegistry
from personal_assistant.core.jobs import JobQueuePort
from personal_assistant.core.tasks import TaskService
from personal_assistant.core.tasks.service import EventStreamPort
from personal_assistant.infrastructure.database import (
    PostgresAdapterConfig,
    build_postgres_adapters,
)
from personal_assistant.infrastructure.memory import (
    InMemoryAuditWriter,
    InMemoryEventStream,
    InMemoryJobQueue,
    InMemoryTaskRepository,
)
from personal_assistant.settings import Settings


@dataclass(slots=True)
class Container:
    settings: Settings
    tasks: TaskService
    approvals: ApprovalService
    extension_registry: ExtensionRegistry
    bundled_extensions_root: Path
    jobs: JobQueuePort
    events: EventStreamPort


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings.from_env()
    if settings.storage_backend == "postgres":
        # Explicitly fail rather than silently losing production state in memory.
        build_postgres_adapters(PostgresAdapterConfig(settings.database_url))

    queue = InMemoryJobQueue()
    audit = InMemoryAuditWriter()
    events = InMemoryEventStream()
    task_repository = InMemoryTaskRepository()
    return Container(
        settings=settings,
        tasks=TaskService(
            repository=task_repository,
            queue=queue,
            audit=audit,
            events=events,
        ),
        approvals=ApprovalService(InMemoryApprovalRepository()),
        extension_registry=ExtensionRegistry(),
        bundled_extensions_root=Path(__file__).resolve().parents[2] / "extensions",
        jobs=queue,
        events=events,
    )


@lru_cache(maxsize=1)
def default_container() -> Container:
    return build_container()
