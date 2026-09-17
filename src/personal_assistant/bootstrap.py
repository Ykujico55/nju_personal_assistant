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
from personal_assistant.core.extensions import (
    ExtensionOperationStore,
    ExtensionRegistry,
    ExtensionSupervisorService,
)
from personal_assistant.core.extensions.lifecycle import (
    ExtensionDataStore,
    InstallCoordinator,
    LifecycleManager,
    LifecycleStore,
)
from personal_assistant.core.jobs import JobQueuePort, SideEffectOutboxPort
from personal_assistant.core.models import (
    DisclosureConsentService,
    ModelProvider,
    ModelRouter,
    RecipientIdentity,
)
from personal_assistant.core.secrets import SecretHandle, SecretStorePort
from personal_assistant.core.tasks import TaskService
from personal_assistant.core.tasks.service import EventStreamPort
from personal_assistant.infrastructure.database import (
    PostgresAdapterConfig,
    PostgresAdapters,
    PostgresExtensionOperationStore,
    PostgresVersionCatalog,
    build_postgres_adapters,
)
from personal_assistant.infrastructure.extensions import (
    CompatibleVersionOperator,
    LocalArtifactStager,
    PostgresExtensionDataStore,
    ProcessContractVerifier,
    ProcessRuntimeSupervisor,
    VenvArtifactInstaller,
    VersionCatalog,
)
from personal_assistant.infrastructure.extensions.data import InMemoryExtensionDataStore
from personal_assistant.infrastructure.memory import (
    InMemoryAuditWriter,
    InMemoryDisclosureConsentStore,
    InMemoryEventStream,
    InMemoryExtensionOperationStore,
    InMemoryJobQueue,
    InMemoryLifecycleStore,
    InMemorySecretStore,
    InMemorySideEffectOutbox,
    InMemoryTaskRepository,
    InMemoryVersionCatalog,
)
from personal_assistant.infrastructure.models import (
    OllamaChatProvider,
    OllamaConfig,
    OpenAICompatibleChatProvider,
    OpenAICompatibleConfig,
)
from personal_assistant.infrastructure.secrets import UnavailableSecretStore
from personal_assistant.infrastructure.storage import NullStorageLifecycle, StorageLifecycle
from personal_assistant.settings import Settings


@dataclass(slots=True)
class Container:
    settings: Settings
    tasks: TaskService
    approvals: ApprovalService
    extension_registry: ExtensionRegistry
    extension_supervisor: ExtensionSupervisorService
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
    disclosures: DisclosureConsentService
    model_router: ModelRouter | None

    async def aclose(self) -> None:
        """Release adapter-owned transports; storage keeps its own lifecycle."""

        if self.model_router is not None:
            await self.model_router.aclose()


def _bundled_extensions_root() -> Path:
    return Path(__file__).resolve().parents[2] / "extensions"


def build_container(
    settings: Settings | None = None, *, secret_store: SecretStorePort | None = None
) -> Container:
    settings = settings or Settings.from_env()
    settings.validate()
    if settings.storage_backend == "postgres":
        return _build_postgres_container(settings, secret_store)
    return _build_memory_container(settings, secret_store)


def _build_model_providers(
    settings: Settings, secret_store: SecretStorePort
) -> list[ModelProvider]:
    """Build providers from validated settings; none configured means no path."""

    providers: list[ModelProvider] = []
    if settings.model_local_base_url and settings.model_local_model:
        providers.append(
            OllamaChatProvider(
                OllamaConfig(
                    provider_id=settings.model_local_provider_id,
                    model_id=settings.model_local_model,
                    base_url=settings.model_local_base_url,
                    timeout_seconds=settings.model_local_timeout_seconds,
                )
            )
        )
    if (
        settings.model_remote_base_url
        and settings.model_remote_model
        and settings.model_remote_secret_handle
    ):
        providers.append(
            OpenAICompatibleChatProvider(
                OpenAICompatibleConfig(
                    provider_id=settings.model_remote_provider_id,
                    model_id=settings.model_remote_model,
                    base_url=settings.model_remote_base_url,
                    timeout_seconds=settings.model_remote_timeout_seconds,
                    secret_handle=SecretHandle(
                        id=settings.model_remote_secret_handle, kind="model_api_key"
                    ),
                ),
                secret_store=secret_store,
            )
        )
    return providers


def _remote_recipients(
    providers: list[ModelProvider],
) -> dict[str, RecipientIdentity]:
    """Only configured remote providers can ever receive a disclosure consent."""

    return {
        provider.provider_id: provider.recipient
        for provider in providers
        if provider.is_remote
    }


def _build_model_router(
    settings: Settings,
    *,
    providers: list[ModelProvider],
    disclosures: DisclosureConsentService,
    audit: AuditWriterPort,
) -> ModelRouter | None:
    if not providers:
        return None
    return ModelRouter(
        tuple(providers),
        disclosure=disclosures,
        default_local_fallback_id=settings.model_local_fallback_provider_id,
        audit=audit,
    )


def _staging_and_install_roots(settings: Settings) -> tuple[Path, Path]:
    return settings.extension_root / "staging", settings.extension_root / "installed"


def _build_supervisor(
    settings: Settings,
    *,
    registry: ExtensionRegistry,
    store: LifecycleStore,
    operations: ExtensionOperationStore,
    version_catalog: VersionCatalog,
    data_store: ExtensionDataStore,
) -> ExtensionSupervisorService:
    stager = LocalArtifactStager(_staging_and_install_roots(settings)[0])
    installer = VenvArtifactInstaller(
        install_root=_staging_and_install_roots(settings)[1], stager=stager
    )
    runtime = ProcessRuntimeSupervisor()
    coordinator = InstallCoordinator(stager, installer, ProcessContractVerifier(), store)
    manager = LifecycleManager(
        store,
        registry,
        runtime,
        installer,
        data_store,
        CompatibleVersionOperator(version_catalog),
    )
    return ExtensionSupervisorService(
        coordinator=coordinator,
        manager=manager,
        registry=registry,
        store=store,
        operations=operations,
        runtime=runtime,
    )


def _build_postgres_container(
    settings: Settings, secret_store: SecretStorePort | None
) -> Container:
    adapters: PostgresAdapters = build_postgres_adapters(
        PostgresAdapterConfig.from_env(settings.database_url)
    )
    registry = ExtensionRegistry()
    operations = PostgresExtensionOperationStore(adapters.database)
    credential_store: SecretStorePort = secret_store or UnavailableSecretStore()
    providers = _build_model_providers(settings, credential_store)
    disclosures = DisclosureConsentService(
        adapters.disclosure_consents, recipients=_remote_recipients(providers)
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
        extension_registry=registry,
        extension_supervisor=_build_supervisor(
            settings,
            registry=registry,
            store=adapters.lifecycle_store,
            operations=operations,
            version_catalog=PostgresVersionCatalog(adapters.database),
            data_store=PostgresExtensionDataStore(adapters.database),
        ),
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
        disclosures=disclosures,
        model_router=_build_model_router(
            settings,
            providers=providers,
            disclosures=disclosures,
            audit=adapters.audit_writer,
        ),
    )


def _build_memory_container(
    settings: Settings, secret_store: SecretStorePort | None
) -> Container:
    queue = InMemoryJobQueue()
    audit = InMemoryAuditWriter()
    events = InMemoryEventStream()
    task_repository = InMemoryTaskRepository()
    approvals = ApprovalService(InMemoryApprovalRepository())
    registry = ExtensionRegistry()
    lifecycle_store = InMemoryLifecycleStore()
    operations = InMemoryExtensionOperationStore()
    credential_store = secret_store or InMemorySecretStore()
    providers = _build_model_providers(settings, credential_store)
    disclosures = DisclosureConsentService(
        InMemoryDisclosureConsentStore(), recipients=_remote_recipients(providers)
    )
    return Container(
        settings=settings,
        tasks=TaskService(
            repository=task_repository,
            queue=queue,
            audit=audit,
            events=events,
        ),
        approvals=approvals,
        extension_registry=registry,
        extension_supervisor=_build_supervisor(
            settings,
            registry=registry,
            store=lifecycle_store,
            operations=operations,
            version_catalog=InMemoryVersionCatalog(),
            data_store=InMemoryExtensionDataStore(),
        ),
        bundled_extensions_root=_bundled_extensions_root(),
        jobs=queue,
        events=events,
        storage=NullStorageLifecycle(),
        run_repository=InMemoryRunRepository(),
        checkpoint_store=InMemoryCheckpointStore(),
        observation_store=InMemoryObservationStore(),
        audit_writer=audit,
        side_effect_outbox=InMemorySideEffectOutbox(approvals),
        lifecycle_store=lifecycle_store,
        disclosures=disclosures,
        model_router=_build_model_router(
            settings,
            providers=providers,
            disclosures=disclosures,
            audit=audit,
        ),
    )


@lru_cache(maxsize=1)
def default_container() -> Container:
    return build_container()
