"""Deterministic adapters for local development and tests only."""

from .audit import InMemoryAuditWriter
from .disclosures import InMemoryDisclosureConsentStore
from .extension_data import UnavailableExtensionDataAccess
from .extensions import InMemoryLifecycleStore
from .job_queue import InMemoryJobQueue
from .operations import InMemoryExtensionOperationStore
from .outbox import InMemorySideEffectOutbox
from .secrets import InMemorySecretStore
from .tasks import InMemoryEventStream, InMemoryTaskRepository
from .versions import InMemoryVersionCatalog

__all__ = [
    "InMemoryAuditWriter",
    "InMemoryDisclosureConsentStore",
    "InMemoryEventStream",
    "InMemoryExtensionOperationStore",
    "InMemoryJobQueue",
    "InMemoryLifecycleStore",
    "InMemorySecretStore",
    "InMemorySideEffectOutbox",
    "InMemoryTaskRepository",
    "InMemoryVersionCatalog",
    "UnavailableExtensionDataAccess",
]
