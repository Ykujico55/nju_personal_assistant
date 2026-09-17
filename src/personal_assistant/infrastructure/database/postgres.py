"""Real PostgreSQL adapter set for the F01 persistence kernel.

The factory is synchronous and performs no I/O; the FastAPI/Worker startup
lifecycle calls :meth:`PostgresAdapters.startup` which connects and applies
migrations, failing closed on any error.
"""

from __future__ import annotations

from dataclasses import dataclass

from .approval_repository import PostgresApprovalRepository
from .audit_writer import PostgresAuditWriter
from .config import PostgresAdapterConfig, normalize_dsn
from .connection import PostgresDatabase
from .disclosure_consents import PostgresDisclosureConsentStore
from .job_queue import PostgresJobQueue
from .lifecycle_store import PostgresLifecycleStore
from .outbox import PostgresSideEffectOutbox
from .run_repository import (
    PostgresCheckpointStore,
    PostgresObservationStore,
    PostgresRunRepository,
)
from .task_repository import PostgresEventStream, PostgresTaskRepository


class PostgresAdapterNotImplemented(RuntimeError):
    """Retained for import compatibility; F01 no longer raises it."""

    code = "POSTGRES_ADAPTER_NOT_IMPLEMENTED"


@dataclass(slots=True)
class PostgresAdapters:
    config: PostgresAdapterConfig
    database: PostgresDatabase
    task_repository: PostgresTaskRepository
    event_stream: PostgresEventStream
    job_queue: PostgresJobQueue
    approval_repository: PostgresApprovalRepository
    run_repository: PostgresRunRepository
    checkpoint_store: PostgresCheckpointStore
    observation_store: PostgresObservationStore
    audit_writer: PostgresAuditWriter
    side_effect_outbox: PostgresSideEffectOutbox
    lifecycle_store: PostgresLifecycleStore
    disclosure_consents: PostgresDisclosureConsentStore

    async def startup(self) -> tuple[str, ...]:
        return await self.database.startup()

    async def close(self) -> None:
        await self.event_stream.close()
        await self.database.close()


def build_postgres_adapters(config: PostgresAdapterConfig) -> PostgresAdapters:
    database = PostgresDatabase(config)
    return PostgresAdapters(
        config=config,
        database=database,
        task_repository=PostgresTaskRepository(database, owner_id=config.owner_id),
        event_stream=PostgresEventStream(database),
        job_queue=PostgresJobQueue(database),
        approval_repository=PostgresApprovalRepository(database, owner_id=config.owner_id),
        run_repository=PostgresRunRepository(database),
        checkpoint_store=PostgresCheckpointStore(database),
        observation_store=PostgresObservationStore(database),
        audit_writer=PostgresAuditWriter(database),
        side_effect_outbox=PostgresSideEffectOutbox(database),
        lifecycle_store=PostgresLifecycleStore(database),
        disclosure_consents=PostgresDisclosureConsentStore(database),
    )


__all__ = [
    "PostgresAdapterConfig",
    "PostgresAdapterNotImplemented",
    "PostgresAdapters",
    "build_postgres_adapters",
    "normalize_dsn",
]
