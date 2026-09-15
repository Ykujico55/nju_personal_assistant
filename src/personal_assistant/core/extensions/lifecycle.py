"""Extension installation confirmation barrier and lifecycle orchestration.

The concrete venv builder, process supervisor, and persistence adapters live in
infrastructure.  Keeping them behind ports makes the order of trust-sensitive
operations unit-testable without running third-party code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from .errors import ConfirmationRequiredError, ExtensionError, InvalidLifecycleTransition
from .manifest import ExtensionManifest, ManifestParser, compute_artifact_hash
from .models import ExtensionRecord, ExtensionState
from .registry import ExtensionRegistry

TRUST_WARNING = (
    "安装即表示你信任此扩展以当前操作系统用户身份运行；独立 venv、Worker 和 "
    "JSON-RPC 仅隔离依赖与崩溃，不是恶意代码安全沙箱。"
)


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    source: str
    root: Path
    artifact_hash: str


@dataclass(frozen=True, slots=True)
class InstalledArtifact:
    install_path: Path
    runtime_key: str


@dataclass(frozen=True, slots=True)
class InstallationPreview:
    plan_id: str
    confirmation_nonce: str
    expires_at: datetime
    source: str
    artifact_hash: str
    extension_id: str
    extension_name: str
    extension_version: str
    entrypoint: str
    slots: Mapping[str, tuple[str, ...]]
    tool_risks: Mapping[str, str]
    required_capabilities: tuple[str, ...]
    optional_capabilities: tuple[str, ...]
    warning: str
    preview_hash: str


@dataclass(frozen=True, slots=True)
class InstallationConfirmation:
    """Exact user confirmation created by the local management boundary."""

    plan_id: str
    confirmation_nonce: str
    preview_hash: str
    actor: str
    confirmed_at: datetime
    accepted_warning: bool


@dataclass(frozen=True, slots=True)
class PurgePreview:
    extension_id: str
    confirmation_nonce: str
    preview_hash: str
    data_namespaces: tuple[str, ...]
    warning: str


@dataclass(frozen=True, slots=True)
class PurgeConfirmation:
    extension_id: str
    confirmation_nonce: str
    preview_hash: str
    actor: str
    confirmed_at: datetime


@dataclass(slots=True)
class _PendingInstall:
    staged: StagedArtifact
    manifest: ExtensionManifest
    preview: InstallationPreview
    consumed: bool = False


class ArtifactStager(Protocol):
    """Copies/fetches an exact artifact without importing or executing it."""

    async def stage(self, source: str) -> StagedArtifact: ...

    async def discard(self, staged: StagedArtifact) -> None: ...


class ArtifactInstaller(Protocol):
    """Creates the venv and runs build/install steps after confirmation only."""

    async def install(
        self, staged: StagedArtifact, manifest: ExtensionManifest
    ) -> InstalledArtifact: ...

    async def uninstall_code(self, record: ExtensionRecord) -> None: ...

    async def clean_failed_install(self, staged: StagedArtifact) -> None: ...


class ContractVerifier(Protocol):
    """Starts a temporary worker and checks handshake, health, and contracts."""

    async def verify(
        self,
        installed: InstalledArtifact,
        manifest: ExtensionManifest,
    ) -> None: ...


class LifecycleStore(Protocol):
    async def get(self, extension_id: str) -> ExtensionRecord | None: ...

    async def save(self, record: ExtensionRecord) -> None: ...


class RuntimeSupervisor(Protocol):
    async def start(self, record: ExtensionRecord) -> None: ...

    async def health(self, record: ExtensionRecord) -> bool: ...

    async def drain(self, record: ExtensionRecord, deadline_epoch: float) -> None: ...

    async def stop(self, record: ExtensionRecord) -> None: ...


class ExtensionDataStore(Protocol):
    async def namespaces(self, extension_id: str) -> Sequence[str]: ...

    async def purge(self, extension_id: str) -> None: ...


class VersionOperator(Protocol):
    """Infrastructure hook for side-by-side upgrade and verified rollback."""

    async def activate(self, old: ExtensionRecord, candidate: ExtensionRecord) -> None: ...

    async def rollback(self, current: ExtensionRecord) -> ExtensionRecord: ...


class InstallCoordinator:
    """Enforces static inspection -> exact user confirmation -> execution."""

    def __init__(
        self,
        stager: ArtifactStager,
        installer: ArtifactInstaller,
        verifier: ContractVerifier,
        store: LifecycleStore,
        *,
        parser: ManifestParser | None = None,
        clock: Callable[[], datetime] | None = None,
        confirmation_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self._stager = stager
        self._installer = installer
        self._verifier = verifier
        self._store = store
        self._parser = parser or ManifestParser()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._confirmation_ttl = confirmation_ttl
        self._pending: dict[str, _PendingInstall] = {}

    async def prepare(self, source: str) -> InstallationPreview:
        """Stage and inspect as data.  No extension/build code may run here."""

        staged = await self._stager.stage(source)
        computed_hash = compute_artifact_hash(staged.root)
        if computed_hash != staged.artifact_hash:
            await self._stager.discard(staged)
            raise ExtensionError("staged artifact hash does not match its content")
        try:
            manifest = self._parser.parse(staged.root)
            existing = await self._store.get(manifest.id)
            if existing is not None and existing.state is not ExtensionState.UNINSTALLED:
                raise ExtensionError(f"extension is already installed: {manifest.id}")
            preview = _make_install_preview(
                staged,
                manifest,
                expires_at=self._clock() + self._confirmation_ttl,
            )
        except Exception:
            await self._stager.discard(staged)
            raise
        self._pending[preview.plan_id] = _PendingInstall(staged, manifest, preview)
        await self._store.save(
            ExtensionRecord(
                manifest=manifest,
                artifact_hash=staged.artifact_hash,
                state=ExtensionState.STAGED,
            )
        )
        return preview

    async def reject(self, plan_id: str) -> ExtensionRecord:
        plan = self._pending.pop(plan_id, None)
        if plan is None or plan.consumed:
            raise ExtensionError("installation plan is missing or already consumed")
        plan.consumed = True
        await self._stager.discard(plan.staged)
        record = ExtensionRecord(
            manifest=plan.manifest,
            artifact_hash=plan.staged.artifact_hash,
            state=ExtensionState.REJECTED,
        )
        await self._store.save(record)
        return record

    async def install(
        self,
        plan_id: str,
        confirmation: InstallationConfirmation | None,
    ) -> ExtensionRecord:
        plan = self._pending.get(plan_id)
        if plan is None or plan.consumed:
            raise ConfirmationRequiredError("installation plan is missing or already consumed")
        # This guard is intentionally before the first call to installer or verifier.
        self._validate_confirmation(plan.preview, confirmation)
        plan.consumed = True
        try:
            installed = await self._installer.install(plan.staged, plan.manifest)
            await self._verifier.verify(installed, plan.manifest)
        except Exception:
            await self._installer.clean_failed_install(plan.staged)
            record = ExtensionRecord(
                manifest=plan.manifest,
                artifact_hash=plan.staged.artifact_hash,
                state=ExtensionState.REJECTED,
            )
            await self._store.save(record)
            raise
        finally:
            self._pending.pop(plan_id, None)

        record = ExtensionRecord(
            manifest=plan.manifest,
            artifact_hash=plan.staged.artifact_hash,
            state=ExtensionState.INSTALLED_DISABLED,
            install_path=str(installed.install_path),
            data_retained=True,
        )
        await self._store.save(record)
        return record

    def _validate_confirmation(
        self,
        preview: InstallationPreview,
        confirmation: InstallationConfirmation | None,
    ) -> None:
        if confirmation is None:
            raise ConfirmationRequiredError("explicit installation confirmation is required")
        if not confirmation.accepted_warning:
            raise ConfirmationRequiredError("the trusted-code warning was not accepted")
        if not confirmation.actor.strip():
            raise ConfirmationRequiredError("confirmation actor is required")
        expected = (
            preview.plan_id,
            preview.confirmation_nonce,
            preview.preview_hash,
        )
        actual = (
            confirmation.plan_id,
            confirmation.confirmation_nonce,
            confirmation.preview_hash,
        )
        if actual != expected:
            raise ConfirmationRequiredError("confirmation does not match the exact preview")
        now = self._clock()
        if confirmation.confirmed_at > now + timedelta(minutes=1):
            raise ConfirmationRequiredError("confirmation timestamp is in the future")
        if confirmation.confirmed_at > preview.expires_at or now > preview.expires_at:
            raise ConfirmationRequiredError("installation confirmation has expired")


class LifecycleManager:
    """Coordinates capability publication with worker lifecycle changes."""

    def __init__(
        self,
        store: LifecycleStore,
        registry: ExtensionRegistry,
        supervisor: RuntimeSupervisor,
        installer: ArtifactInstaller,
        data_store: ExtensionDataStore,
        version_operator: VersionOperator,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._supervisor = supervisor
        self._installer = installer
        self._data_store = data_store
        self._version_operator = version_operator
        self._clock = clock or (lambda: datetime.now(UTC))
        self._purges: dict[str, PurgePreview] = {}

    async def enable(self, extension_id: str) -> ExtensionRecord:
        record = await self._require(extension_id)
        _require_state(
            record,
            {
                ExtensionState.INSTALLED_DISABLED,
                ExtensionState.DISABLED,
                ExtensionState.ROLLED_BACK,
            },
            "enable",
        )
        starting = replace(record, state=ExtensionState.STARTING)
        await self._store.save(starting)
        published = False
        try:
            await self._supervisor.start(starting)
            if not await self._supervisor.health(starting):
                raise ExtensionError("extension healthcheck failed")
            enabled = replace(starting, state=ExtensionState.ENABLED)
            # Publish only after health succeeds; any later persistence failure revokes it.
            self._registry.enable(enabled)
            published = True
            await self._store.save(enabled)
            return enabled
        except BaseException:
            if published:
                self._registry.disable(extension_id)
            # Cleanup must finish even when the caller was cancelled.
            await asyncio.shield(self._supervisor.stop(starting))
            quarantined = replace(starting, state=ExtensionState.QUARANTINED)
            await asyncio.shield(self._store.save(quarantined))
            raise

    async def disable(self, extension_id: str, *, deadline_epoch: float) -> ExtensionRecord:
        record = await self._require(extension_id)
        _require_state(record, {ExtensionState.ENABLED}, "disable")
        # Revoke new dispatch first.  Runs holding an older snapshot may finish only
        # through the supervisor's bounded drain protocol.
        self._registry.disable(extension_id)
        draining = replace(record, state=ExtensionState.DRAINING)
        await self._store.save(draining)
        try:
            await self._supervisor.drain(draining, deadline_epoch)
        finally:
            await self._supervisor.stop(draining)
        disabled = replace(draining, state=ExtensionState.DISABLED)
        await self._store.save(disabled)
        return disabled

    async def uninstall(self, extension_id: str) -> ExtensionRecord:
        record = await self._require(extension_id)
        if record.state is ExtensionState.ENABLED:
            record = await self.disable(extension_id, deadline_epoch=self._clock().timestamp() + 30)
        _require_state(
            record,
            {
                ExtensionState.INSTALLED_DISABLED,
                ExtensionState.DISABLED,
                ExtensionState.QUARANTINED,
                ExtensionState.ROLLED_BACK,
            },
            "uninstall",
        )
        self._registry.disable(extension_id)
        uninstalling = replace(record, state=ExtensionState.UNINSTALLING)
        await self._store.save(uninstalling)
        await self._supervisor.stop(uninstalling)
        await self._installer.uninstall_code(uninstalling)
        removed = replace(
            uninstalling,
            state=ExtensionState.UNINSTALLED,
            install_path=None,
            data_retained=True,
            tombstone=True,
        )
        await self._store.save(removed)
        return removed

    async def prepare_purge(self, extension_id: str) -> PurgePreview:
        record = await self._require(extension_id)
        _require_state(record, {ExtensionState.UNINSTALLED}, "purge")
        namespaces = tuple(await self._data_store.namespaces(extension_id))
        nonce = secrets.token_urlsafe(24)
        warning = "永久清除不可恢复；卸载本身不会删除这些数据。"
        payload: dict[str, object] = {
            "extension_id": extension_id,
            "namespaces": namespaces,
            "nonce": nonce,
            "warning": warning,
        }
        preview = PurgePreview(
            extension_id=extension_id,
            confirmation_nonce=nonce,
            preview_hash=_hash_payload(payload),
            data_namespaces=namespaces,
            warning=warning,
        )
        self._purges[extension_id] = preview
        return preview

    async def purge(self, confirmation: PurgeConfirmation) -> ExtensionRecord:
        preview = self._purges.get(confirmation.extension_id)
        if preview is None:
            raise ConfirmationRequiredError("prepare a purge preview first")
        if (
            confirmation.confirmation_nonce != preview.confirmation_nonce
            or confirmation.preview_hash != preview.preview_hash
            or not confirmation.actor.strip()
        ):
            raise ConfirmationRequiredError("purge confirmation does not match its preview")
        record = await self._require(confirmation.extension_id)
        _require_state(record, {ExtensionState.UNINSTALLED}, "purge")
        self._purges.pop(confirmation.extension_id, None)
        await self._data_store.purge(confirmation.extension_id)
        purged = replace(record, data_retained=False)
        await self._store.save(purged)
        return purged

    async def upgrade(
        self,
        extension_id: str,
        candidate: ExtensionRecord,
    ) -> ExtensionRecord:
        """Activate an already confirmed, installed, and contract-tested candidate."""

        current = await self._require(extension_id)
        _require_state(current, {ExtensionState.ENABLED, ExtensionState.DISABLED}, "upgrade")
        if candidate.manifest.id != extension_id:
            raise ExtensionError("upgrade candidate has a different extension id")
        if candidate.state is not ExtensionState.INSTALLED_DISABLED:
            raise ExtensionError("upgrade candidate must be installed and contract-tested")
        was_enabled = current.state is ExtensionState.ENABLED
        if was_enabled:
            current = await self.disable(
                extension_id, deadline_epoch=self._clock().timestamp() + 30
            )
        upgrading = replace(current, state=ExtensionState.UPGRADING)
        await self._store.save(upgrading)
        try:
            await self._version_operator.activate(upgrading, candidate)
        except Exception:
            rolled_back = replace(current, state=ExtensionState.ROLLED_BACK)
            await self._store.save(rolled_back)
            if was_enabled:
                await self.enable(extension_id)
            raise
        activated = replace(candidate, state=ExtensionState.DISABLED)
        await self._store.save(activated)
        return await self.enable(extension_id) if was_enabled else activated

    async def rollback(self, extension_id: str) -> ExtensionRecord:
        current = await self._require(extension_id)
        _require_state(
            current,
            {ExtensionState.ENABLED, ExtensionState.DISABLED, ExtensionState.QUARANTINED},
            "rollback",
        )
        if current.state is ExtensionState.ENABLED:
            current = await self.disable(
                extension_id, deadline_epoch=self._clock().timestamp() + 30
            )
        restored = await self._version_operator.rollback(current)
        if restored.manifest.id != extension_id:
            raise ExtensionError("rollback restored a different extension id")
        rolled_back = replace(restored, state=ExtensionState.ROLLED_BACK)
        await self._store.save(rolled_back)
        return rolled_back

    async def _require(self, extension_id: str) -> ExtensionRecord:
        record = await self._store.get(extension_id)
        if record is None:
            raise ExtensionError(f"unknown extension: {extension_id}")
        return record


def _make_install_preview(
    staged: StagedArtifact,
    manifest: ExtensionManifest,
    *,
    expires_at: datetime,
) -> InstallationPreview:
    plan_id = f"install_{secrets.token_urlsafe(18)}"
    nonce = secrets.token_urlsafe(24)
    body = {
        "plan_id": plan_id,
        "nonce": nonce,
        "expires_at": expires_at.isoformat(),
        "source": staged.source,
        "artifact_hash": staged.artifact_hash,
        "extension_id": manifest.id,
        "extension_name": manifest.name,
        "extension_version": manifest.version,
        "entrypoint": manifest.entrypoint,
        "slots": {key: list(value) for key, value in manifest.slots.items()},
        "tool_risks": {tool.id: tool.risk for tool in manifest.tools},
        "required_capabilities": list(manifest.capabilities.required),
        "optional_capabilities": list(manifest.capabilities.optional),
        "warning": TRUST_WARNING,
    }
    return InstallationPreview(
        plan_id=plan_id,
        confirmation_nonce=nonce,
        expires_at=expires_at,
        source=staged.source,
        artifact_hash=staged.artifact_hash,
        extension_id=manifest.id,
        extension_name=manifest.name,
        extension_version=manifest.version,
        entrypoint=manifest.entrypoint,
        slots=manifest.slots,
        tool_risks={tool.id: tool.risk for tool in manifest.tools},
        required_capabilities=manifest.capabilities.required,
        optional_capabilities=manifest.capabilities.optional,
        warning=TRUST_WARNING,
        preview_hash=_hash_payload(body),
    )


def _hash_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _require_state(
    record: ExtensionRecord,
    allowed: set[ExtensionState],
    operation: str,
) -> None:
    if record.state not in allowed:
        rendered = ", ".join(sorted(state.value for state in allowed))
        raise InvalidLifecycleTransition(
            f"cannot {operation} {record.manifest.id} from "
            f"{record.state.value}; expected {rendered}"
        )
