"""Composition root. Business logic must not be added here."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from personal_assistant.core.agent.checkpoint import (
    CheckpointStorePort,
    InMemoryCheckpointStore,
    InMemoryObservationStore,
    InMemoryRunRepository,
    ObservationStorePort,
    RunRepositoryPort,
)
from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.audit import AuditWriterPort
from personal_assistant.core.extensions import ExtensionRegistry
from personal_assistant.core.extensions.lifecycle import LifecycleStore
from personal_assistant.core.jobs import JobQueuePort, SideEffectOutboxPort
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
    InMemoryLifecycleStore,
    InMemorySideEffectOutbox,
    InMemoryTaskRepository,
)
from personal_assistant.infrastructure.storage import NullStorageLifecycle, StorageLifecycle
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
    storage: StorageLifecycle
    run_repository: RunRepositoryPort
    checkpoint_store: CheckpointStorePort
    observation_store: ObservationStorePort
    audit_writer: AuditWriterPort
    side_effect_outbox: SideEffectOutboxPort
    lifecycle_store: LifecycleStore


def _bundled_extensions_root() -> Path:
    return Path(__file__).resolve().parents[2] / "extensions"


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings.from_env()
    if settings.storage_backend == "postgres":
        return _build_postgres_container(settings)
    return _build_memory_container(settings)


def _build_postgres_container(settings: Settings) -> Container:
    adapters = build_postgres_adapters(
        PostgresAdapterConfig.from_env(settings.database_url)
    )
    return Container(
        settings=settings,
        tasks=TaskService(
            repository=adapters.task_repository,
            queue=adapters.job_queue,
            audit=adapters.audit_writer,
            events=adapters.event_stream,
            unit_of_work=adapters.database,
        ),
        approvals=ApprovalService(adapters.approval_repository),
        extension_registry=ExtensionRegistry(),
        bundled_extensions_root=_bundled_extensions_root(),
        jobs=adapters.job_queue,
        events=adapters.event_stream,
        storage=adapters,
        run_repository=adapters.run_repository,
        checkpoint_store=adapters.checkpoint_store,
        observation_store=adapters.observation_store,
        audit_writer=adapters.audit_writer,
        side_effect_outbox=adapters.side_effect_outbox,
        lifecycle_store=adapters.lifecycle_store,
    )


def _build_memory_container(settings: Settings) -> Container:
    queue = InMemoryJobQueue()
    audit = InMemoryAuditWriter()
    events = InMemoryEventStream()
    task_repository = InMemoryTaskRepository()
    approvals = ApprovalService(InMemoryApprovalRepository())
    return Container(
        settings=settings,
        tasks=TaskService(
            repository=task_repository,
            queue=queue,
            audit=audit,
            events=events,
        ),
        approvals=approvals,
        extension_registry=ExtensionRegistry(),
        bundled_extensions_root=_bundled_extensions_root(),
        jobs=queue,
        events=events,
        storage=NullStorageLifecycle(),
        run_repository=InMemoryRunRepository(),
        checkpoint_store=InMemoryCheckpointStore(),
        observation_store=InMemoryObservationStore(),
        audit_writer=audit,
        side_effect_outbox=InMemorySideEffectOutbox(approvals),
        lifecycle_store=InMemoryLifecycleStore(),
    )


@lru_cache(maxsize=1)
def default_container() -> Container:
    return build_container()
