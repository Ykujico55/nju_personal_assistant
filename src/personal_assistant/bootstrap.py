"""Composition root. Business logic must not be added here."""

from __future__ import annotations

import contextlib
import ssl
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
from personal_assistant.core.extensions.artifact_access import ExtensionArtifactAccess
from personal_assistant.core.extensions.config import ExtensionConfigStore
from personal_assistant.core.extensions.data_access import ExtensionDataAccess
from personal_assistant.core.extensions.descriptors import manifest_tool_descriptors
from personal_assistant.core.extensions.lifecycle import (
    ExtensionDataStore,
    InstallCoordinator,
    LifecycleManager,
    LifecycleStore,
)
from personal_assistant.core.extensions.models import ExtensionState
from personal_assistant.core.jobs import JobQueuePort, SideEffectOutboxPort
from personal_assistant.core.mail import (
    MailAccountRegistry,
    MailDeliveryLedger,
    MailPolicy,
    MailTransportBroker,
)
from personal_assistant.core.models import (
    DisclosureConsentService,
    ModelProvider,
    ModelRouter,
    RecipientIdentity,
)
from personal_assistant.core.secrets import SecretHandle, SecretStorePort
from personal_assistant.core.tasks import TaskService
from personal_assistant.core.tasks.service import EventStreamPort
from personal_assistant.core.tools import ToolGateway, ToolPolicy, ToolRegistry
from personal_assistant.infrastructure.database import (
    PostgresAdapterConfig,
    PostgresAdapters,
    PostgresExtensionDataAccess,
    PostgresExtensionOperationStore,
    PostgresVersionCatalog,
    build_postgres_adapters,
)
from personal_assistant.infrastructure.database.mail_ledger import (
    PostgresMailDeliveryLedger,
)
from personal_assistant.infrastructure.extensions import (
    CompatibleVersionOperator,
    FileExtensionConfigStore,
    LocalArtifactStager,
    PostgresExtensionDataStore,
    ProcessContractVerifier,
    ProcessRuntimeSupervisor,
    VenvArtifactInstaller,
    VersionCatalog,
)
from personal_assistant.infrastructure.extensions.artifact_access import (
    FileExtensionArtifactAccess,
)
from personal_assistant.infrastructure.extensions.data import InMemoryExtensionDataStore
from personal_assistant.infrastructure.filesystem import LocalArtifactBlobStore
from personal_assistant.infrastructure.mail.broker import ConfiguredMailTransportBroker
from personal_assistant.infrastructure.mail.executor import MailSendExecutor
from personal_assistant.infrastructure.mail.host import MailHostCapability
from personal_assistant.infrastructure.mail.owners import MailExecutionOwnerRegistry
from personal_assistant.infrastructure.mail.registry import (
    FileMailAccountRegistry,
    InMemoryMailAccountRegistry,
)
from personal_assistant.infrastructure.memory import (
    InMemoryAuditWriter,
    InMemoryDisclosureConsentStore,
    InMemoryEventStream,
    InMemoryExtensionArtifactAccess,
    InMemoryExtensionOperationStore,
    InMemoryJobQueue,
    InMemoryLifecycleStore,
    InMemoryMailDeliveryLedger,
    InMemorySecretStore,
    InMemorySideEffectOutbox,
    InMemoryTaskRepository,
    InMemoryVersionCatalog,
    UnavailableExtensionDataAccess,
)
from personal_assistant.infrastructure.models import (
    OllamaChatProvider,
    OllamaConfig,
    OpenAICompatibleChatProvider,
    OpenAICompatibleConfig,
)
from personal_assistant.infrastructure.secrets import UnavailableSecretStore
from personal_assistant.infrastructure.storage import NullStorageLifecycle, StorageLifecycle
from personal_assistant.infrastructure.tools import CapabilityRoutingExecutor
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
    extension_config_store: ExtensionConfigStore
    disclosures: DisclosureConsentService
    model_router: ModelRouter | None
    mail_broker: MailTransportBroker
    mail_ledger: MailDeliveryLedger
    mail_host_capability: MailHostCapability
    mail_accounts: MailAccountRegistry
    mail_execution_owners: MailExecutionOwnerRegistry
    extension_artifacts: ExtensionArtifactAccess
    mail_send_executor: MailSendExecutor
    tool_registry: ToolRegistry
    tool_gateway: ToolGateway

    async def refresh_tool_registry(self) -> None:
        """Publish domain descriptors for every ENABLED extension record."""

        records = await self.lifecycle_store.all()
        descriptors = [
            descriptor
            for record in records
            if record.state is ExtensionState.ENABLED and record.manifest is not None
            for descriptor in manifest_tool_descriptors(record.manifest)
        ]
        self.tool_registry.publish(descriptors)

    async def aclose(self) -> None:
        """Release adapter-owned transports; storage keeps its own lifecycle."""

        if self.model_router is not None:
            await self.model_router.aclose()
        await self.mail_broker.aclose()
        with contextlib.suppress(Exception):
            await self.mail_ledger.close()


def _bundled_extensions_root() -> Path:
    return Path(__file__).resolve().parents[2] / "extensions"


def build_container(
    settings: Settings | None = None,
    *,
    secret_store: SecretStorePort | None = None,
    mail_ssl_context: ssl.SSLContext | None = None,
) -> Container:
    settings = settings or Settings.from_env()
    settings.validate()
    if settings.storage_backend == "postgres":
        return _build_postgres_container(settings, secret_store, mail_ssl_context)
    return _build_memory_container(settings, secret_store, mail_ssl_context)


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


def _mail_policy(settings: Settings) -> MailPolicy:
    return MailPolicy(
        allow_send=settings.mail_send_enabled,
        allowed_recipients=frozenset(settings.mail_test_recipients),
        max_message_bytes=settings.mail_max_message_bytes,
    )


def _build_supervisor(
    settings: Settings,
    *,
    registry: ExtensionRegistry,
    store: LifecycleStore,
    operations: ExtensionOperationStore,
    version_catalog: VersionCatalog,
    data_store: ExtensionDataStore,
    data_access: ExtensionDataAccess,
    config_store: ExtensionConfigStore,
    mail_capability: MailHostCapability | None = None,
    mail_send_available: bool = False,
    artifact_access: ExtensionArtifactAccess | None = None,
) -> ExtensionSupervisorService:
    stager = LocalArtifactStager(_staging_and_install_roots(settings)[0])
    installer = VenvArtifactInstaller(
        install_root=_staging_and_install_roots(settings)[1], stager=stager
    )
    runtime = ProcessRuntimeSupervisor(
        data_access=data_access,
        config_store=config_store,
        mail_capability=mail_capability,
        mail_send_available=mail_send_available,
        artifact_access=artifact_access,
    )
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
    settings: Settings,
    secret_store: SecretStorePort | None,
    mail_ssl_context: ssl.SSLContext | None = None,
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
    config_store = FileExtensionConfigStore(settings.extension_root / "config")
    policy = _mail_policy(settings)
    mail_accounts = FileMailAccountRegistry(
        settings.extension_root / "mail" / "accounts.json"
    )
    mail_broker = ConfiguredMailTransportBroker(
        credential_store,
        mail_accounts,
        policy=policy,
        ssl_context=mail_ssl_context,
        max_message_bytes=settings.mail_max_message_bytes,
    )
    ledger = PostgresMailDeliveryLedger(adapters.database)
    mail_owners = MailExecutionOwnerRegistry()
    mail_capability = MailHostCapability(
        mail_broker, mail_accounts, ledger=ledger, owners=mail_owners
    )
    artifacts = FileExtensionArtifactAccess(
        LocalArtifactBlobStore(settings.artifact_root),
        settings.extension_root / "artifact-meta",
    )
    supervisor = _build_supervisor(
        settings,
        registry=registry,
        store=adapters.lifecycle_store,
        operations=operations,
        version_catalog=PostgresVersionCatalog(adapters.database),
        data_store=PostgresExtensionDataStore(adapters.database),
        data_access=PostgresExtensionDataAccess(adapters.database),
        config_store=config_store,
        mail_capability=mail_capability,
        mail_send_available=mail_broker.send_available,
        artifact_access=artifacts,
    )
    approvals = ApprovalService(adapters.approval_repository)
    mail_send = MailSendExecutor(
        broker=mail_broker,
        artifacts=artifacts,
        invoker=supervisor,
        ledger=ledger,
        accounts=mail_accounts,
        policy=policy,
        owners=mail_owners,
    )
    tool_registry = ToolRegistry()
    return Container(
        settings=settings,
        tasks=TaskService(
            repository=adapters.task_repository,
            queue=adapters.job_queue,
            audit=adapters.audit_writer,
            events=adapters.event_stream,
            unit_of_work=adapters.database,
        ),
        approvals=approvals,
        extension_registry=registry,
        extension_supervisor=supervisor,
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
        extension_config_store=config_store,
        disclosures=disclosures,
        model_router=_build_model_router(
            settings,
            providers=providers,
            disclosures=disclosures,
            audit=adapters.audit_writer,
        ),
        mail_broker=mail_broker,
        mail_ledger=ledger,
        mail_host_capability=mail_capability,
        mail_accounts=mail_accounts,
        mail_execution_owners=mail_owners,
        extension_artifacts=artifacts,
        mail_send_executor=mail_send,
        tool_registry=tool_registry,
        tool_gateway=ToolGateway(
            registry=tool_registry,
            policy=ToolPolicy(),
            approvals=approvals,
            executor=CapabilityRoutingExecutor(
                extension_router=supervisor, mail_send=mail_send
            ),
            outbox=adapters.side_effect_outbox,
        ),
    )


def _build_memory_container(
    settings: Settings,
    secret_store: SecretStorePort | None,
    mail_ssl_context: ssl.SSLContext | None = None,
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
    config_store = FileExtensionConfigStore(settings.extension_root / "config")
    policy = _mail_policy(settings)
    mail_accounts = InMemoryMailAccountRegistry()
    mail_broker = ConfiguredMailTransportBroker(
        credential_store,
        mail_accounts,
        policy=policy,
        ssl_context=mail_ssl_context,
        max_message_bytes=settings.mail_max_message_bytes,
    )
    ledger = InMemoryMailDeliveryLedger()
    mail_owners = MailExecutionOwnerRegistry()
    mail_capability = MailHostCapability(
        mail_broker, mail_accounts, ledger=ledger, owners=mail_owners
    )
    artifacts = InMemoryExtensionArtifactAccess()
    supervisor = _build_supervisor(
        settings,
        registry=registry,
        store=lifecycle_store,
        operations=operations,
        version_catalog=InMemoryVersionCatalog(),
        data_store=InMemoryExtensionDataStore(),
        data_access=UnavailableExtensionDataAccess(),
        config_store=config_store,
        mail_capability=mail_capability,
        mail_send_available=mail_broker.send_available,
        artifact_access=artifacts,
    )
    mail_send = MailSendExecutor(
        broker=mail_broker,
        artifacts=artifacts,
        invoker=supervisor,
        ledger=ledger,
        accounts=mail_accounts,
        policy=policy,
        owners=mail_owners,
    )
    tool_registry = ToolRegistry()
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
        extension_supervisor=supervisor,
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
        extension_config_store=config_store,
        disclosures=disclosures,
        model_router=_build_model_router(
            settings,
            providers=providers,
            disclosures=disclosures,
            audit=audit,
        ),
        mail_broker=mail_broker,
        mail_ledger=ledger,
        mail_host_capability=mail_capability,
        mail_accounts=mail_accounts,
        mail_execution_owners=mail_owners,
        extension_artifacts=artifacts,
        mail_send_executor=mail_send,
        tool_registry=tool_registry,
        tool_gateway=ToolGateway(
            registry=tool_registry,
            policy=ToolPolicy(),
            approvals=approvals,
            executor=CapabilityRoutingExecutor(
                extension_router=supervisor, mail_send=mail_send
            ),
            outbox=InMemorySideEffectOutbox(approvals),
        ),
    )


@lru_cache(maxsize=1)
def default_container() -> Container:
    return build_container()
