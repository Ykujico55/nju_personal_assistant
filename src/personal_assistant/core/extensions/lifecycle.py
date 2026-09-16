"""Extension installation confirmation barrier and lifecycle orchestration.

The concrete venv builder, process supervisor, and persistence adapters live in
infrastructure.  Keeping them behind ports makes the order of trust-sensitive
operations unit-testable without running third-party code.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from .async_utils import shield_cleanup
from .errors import (
    ConfirmationRequiredError,
    ExtensionError,
    ExtensionOperationError,
    InvalidLifecycleTransition,
    RpcTimeoutError,
)
from .manifest import ExtensionManifest, ManifestParser, compute_artifact_hash
from .models import ExtensionRecord, ExtensionState
from .registry import ExtensionRegistry

TRUST_WARNING = (
    "安装即表示你信任此扩展以当前操作系统用户身份运行；独立 venv、Worker 和 "
    "JSON-RPC 仅隔离依赖与崩溃，不是恶意代码安全沙箱。"
)

# States from which a fresh install plan may be created again: a rejected
# install or an abandoned (staged) plan must not permanently block the id.
_INSTALLABLE_STATES = frozenset(
    {
        ExtensionState.UNINSTALLED,
        ExtensionState.REJECTED,
        ExtensionState.STAGED,
    }
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
    runtime_root: Path | None = None


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
    mode: str = "install"
    replaces_version: str | None = None


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

    async def uninstall_code(self, record: ExtensionRecord) -> None:
        """Delete every installed version of the extension."""

    async def remove_version(self, record: ExtensionRecord) -> None:
        """Delete exactly the version named by ``record.install_path``."""

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

    async def all(self) -> Sequence[ExtensionRecord]: ...

    async def save(self, record: ExtensionRecord) -> None: ...


class RuntimeSupervisor(Protocol):
    async def start(self, record: ExtensionRecord) -> None: ...

    async def health(self, record: ExtensionRecord) -> bool: ...

    async def drain(
        self, record: ExtensionRecord, deadline_epoch: float
    ) -> Mapping[str, object] | None: ...

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
        """Stage a fresh install.  No extension/build code may run here."""

        return await self._prepare(source, mode="install")

    async def prepare_upgrade(self, source: str) -> InstallationPreview:
        """Stage a side-by-side upgrade candidate; the active record is untouched."""

        return await self._prepare(source, mode="upgrade")

    async def prepare_auto(self, source: str) -> InstallationPreview:
        """Stage and decide between install and upgrade from persisted state."""

        return await self._prepare(source, mode=None)

    async def _prepare(self, source: str, *, mode: str | None) -> InstallationPreview:
        staged = await self._stager.stage(source)
        computed_hash = compute_artifact_hash(staged.root)
        if computed_hash != staged.artifact_hash:
            await self._stager.discard(staged)
            raise ExtensionError("staged artifact hash does not match its content")
        try:
            manifest = self._parser.parse(staged.root)
            existing = await self._store.get(manifest.id)
            replaces: str | None = None
            if mode == "install":
                if existing is not None and existing.state not in _INSTALLABLE_STATES:
                    raise ExtensionError(f"extension is already installed: {manifest.id}")
            elif mode == "upgrade":
                replaces = self._require_upgrade_target(existing, manifest)
            else:
                if existing is None or existing.state in _INSTALLABLE_STATES:
                    mode = "install"
                else:
                    replaces = self._require_upgrade_target(existing, manifest)
                    mode = "upgrade"
            assert mode is not None
            preview = _make_install_preview(
                staged,
                manifest,
                expires_at=self._clock() + self._confirmation_ttl,
                mode=mode,
                replaces_version=replaces,
            )
        except Exception:
            await self._stager.discard(staged)
            raise
        # A newer preview supersedes any unconfirmed plan for the same extension.
        await self._drop_pending(manifest.id)
        self._pending[preview.plan_id] = _PendingInstall(staged, manifest, preview)
        if mode == "install":
            await self._store.save(
                ExtensionRecord(
                    manifest=manifest,
                    artifact_hash=staged.artifact_hash,
                    state=ExtensionState.STAGED,
                )
            )
        return preview

    async def _drop_pending(self, extension_id: str) -> None:
        stale = [
            plan_id
            for plan_id, plan in self._pending.items()
            if plan.manifest.id == extension_id
        ]
        for plan_id in stale:
            plan = self._pending.pop(plan_id, None)
            if plan is not None and not plan.consumed:
                plan.consumed = True
                await self._discard_staged(plan.staged)

    def _require_upgrade_target(
        self, existing: ExtensionRecord | None, manifest: ExtensionManifest
    ) -> str:
        if existing is None or existing.state is ExtensionState.UNINSTALLED:
            raise ExtensionError(f"no installed extension to upgrade: {manifest.id}")
        if existing.state not in {
            ExtensionState.ENABLED,
            ExtensionState.DISABLED,
            ExtensionState.INSTALLED_DISABLED,
            ExtensionState.QUARANTINED,
            ExtensionState.ROLLED_BACK,
        }:
            raise InvalidLifecycleTransition(
                f"cannot stage an upgrade from {existing.state.value}"
            )
        if _semver_key(manifest.version) <= _semver_key(existing.manifest.version):
            raise ExtensionError(
                "upgrade candidate must declare a version newer than "
                f"{existing.manifest.version}"
            )
        if manifest.state_schema_version < existing.manifest.state_schema_version:
            raise ExtensionError(
                "upgrade candidate would move the extension data schema backwards; "
                "use rollback for retained older versions"
            )
        return existing.manifest.version

    def preview_for(self, plan_id: str) -> InstallationPreview:
        plan = self._pending.get(plan_id)
        if plan is None:
            raise ExtensionOperationError(
                "PLAN_NOT_FOUND", "installation plan is missing or already consumed"
            )
        return plan.preview

    def validate(
        self,
        plan_id: str,
        confirmation: InstallationConfirmation | None,
    ) -> None:
        """Check confirmation binding without consuming the plan."""

        plan = self._pending.get(plan_id)
        if plan is None or plan.consumed:
            raise ExtensionOperationError(
                "PLAN_NOT_FOUND", "installation plan is missing or already consumed"
            )
        self._validate_confirmation(plan.preview, confirmation)

    async def reject(self, plan_id: str) -> ExtensionRecord:
        plan = self._pending.pop(plan_id, None)
        if plan is None or plan.consumed:
            raise ExtensionOperationError(
                "PLAN_NOT_FOUND", "installation plan is missing or already consumed"
            )
        plan.consumed = True
        await self._stager.discard(plan.staged)
        if plan.preview.mode == "upgrade":
            # Rejecting an upgrade candidate must never overwrite the active
            # version with a REJECTED record.
            current = await self._store.get(plan.manifest.id)
            if current is None:
                raise ExtensionError(
                    f"extension is no longer installed: {plan.manifest.id}"
                )
            return current
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
        """Install a fresh plan; the resulting record is persisted disabled."""

        return await self._execute_install(
            plan_id, confirmation, expected_mode="install", persist=True
        )

    async def install_candidate(
        self,
        plan_id: str,
        confirmation: InstallationConfirmation | None,
    ) -> ExtensionRecord:
        """Install and contract-test an upgrade candidate without persisting it.

        The caller activates the returned ``INSTALLED_DISABLED`` candidate through
        ``LifecycleManager.upgrade``; a failure there must leave the active record
        and its code untouched.
        """

        return await self._execute_install(
            plan_id, confirmation, expected_mode="upgrade", persist=False
        )

    async def discard_candidate(self, record: ExtensionRecord) -> None:
        await self._installer.remove_version(record)

    async def discard_staged_record(self, record: ExtensionRecord) -> None:
        """Best-effort cleanup of an unconfirmed staged tree after a restart."""

        if record.install_path is not None:
            return
        root = Path(record.manifest.root)
        if not root.exists():
            return
        await self._discard_staged(
            StagedArtifact(record.manifest.id, root, record.artifact_hash)
        )

    async def _execute_install(
        self,
        plan_id: str,
        confirmation: InstallationConfirmation | None,
        *,
        expected_mode: str,
        persist: bool,
    ) -> ExtensionRecord:
        plan = self._pending.get(plan_id)
        if plan is None or plan.consumed:
            raise ConfirmationRequiredError("installation plan is missing or already consumed")
        if plan.preview.mode != expected_mode:
            raise ExtensionOperationError(
                "PLAN_MODE_MISMATCH",
                f"plan mode is {plan.preview.mode!r}, expected {expected_mode!r}",
            )
        # This guard is intentionally before the first call to installer or verifier.
        self._validate_confirmation(plan.preview, confirmation)
        await self._verify_staged_unchanged(plan)
        if expected_mode == "upgrade":
            await self._verify_upgrade_baseline(plan)
        plan.consumed = True
        installed: InstalledArtifact | None = None
        try:
            installed = await self._installer.install(plan.staged, plan.manifest)
            await self._verifier.verify(installed, plan.manifest)
            await self._discard_staged(plan.staged)
            record = ExtensionRecord(
                manifest=replace(
                    plan.manifest, root=installed.runtime_root or installed.install_path
                ),
                artifact_hash=plan.staged.artifact_hash,
                state=ExtensionState.INSTALLED_DISABLED,
                install_path=str(installed.install_path),
                data_retained=True,
            )
            if persist:
                await self._store.save(record)
            return record
        except BaseException:
            # Failures, timeouts and cancellation all run the same shielded
            # cleanup: stop nothing that survives, remove the version directory,
            # discard staging and leave a recoverable REJECTED record.
            await shield_cleanup(self._abort_install(plan, installed, persist))
            raise

    async def _abort_install(
        self,
        plan: _PendingInstall,
        installed: InstalledArtifact | None,
        persist: bool,
    ) -> None:
        if installed is not None:
            # Remove the half-verified version so a failed install leaves no code
            # that the registry could ever pick up.
            record = ExtensionRecord(
                manifest=replace(
                    plan.manifest, root=installed.runtime_root or installed.install_path
                ),
                artifact_hash=plan.staged.artifact_hash,
                state=ExtensionState.INSTALLED_DISABLED,
                install_path=str(installed.install_path),
            )
            with contextlib.suppress(Exception):
                await self._installer.remove_version(record)
        with contextlib.suppress(Exception):
            await self._installer.clean_failed_install(plan.staged)
        await self._discard_staged(plan.staged)
        if persist:
            with contextlib.suppress(Exception):
                await self._store.save(
                    ExtensionRecord(
                        manifest=plan.manifest,
                        artifact_hash=plan.staged.artifact_hash,
                        state=ExtensionState.REJECTED,
                    )
                )

    async def _verify_staged_unchanged(self, plan: _PendingInstall) -> None:
        current_hash = compute_artifact_hash(plan.staged.root)
        if current_hash != plan.preview.artifact_hash:
            self._pending.pop(plan.preview.plan_id, None)
            await self._discard_staged(plan.staged)
            raise ConfirmationRequiredError(
                "staged artifact changed after preview; confirm the new content explicitly"
            )

    async def _verify_upgrade_baseline(self, plan: _PendingInstall) -> None:
        """Bind the confirmation to the active version at execution time."""

        current = await self._store.get(plan.manifest.id)
        baseline = plan.preview.replaces_version
        if current is None or current.state is ExtensionState.UNINSTALLED:
            raise ExtensionOperationError(
                "PLAN_BASELINE_CHANGED",
                "the extension is no longer installed; create a new plan",
            )
        if baseline is None or current.manifest.version != baseline:
            raise ExtensionOperationError(
                "PLAN_BASELINE_CHANGED",
                f"active version changed to {current.manifest.version!r} after the preview",
            )
        if _semver_key(plan.manifest.version) <= _semver_key(current.manifest.version):
            raise ExtensionOperationError(
                "PLAN_BASELINE_CHANGED",
                "upgrade candidate is no longer newer than the active version",
            )
        if plan.manifest.state_schema_version < current.manifest.state_schema_version:
            raise ExtensionOperationError(
                "PLAN_BASELINE_CHANGED",
                "upgrade candidate would move the extension data schema backwards",
            )

    async def _discard_staged(self, staged: StagedArtifact) -> None:
        # Cleanup must never turn a completed install into a reported failure.
        with contextlib.suppress(ExtensionError, OSError):
            await self._stager.discard(staged)

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
                # QUARANTINED is repairable: start and health are re-checked below.
                ExtensionState.QUARANTINED,
            },
            "enable",
        )
        return await self._start_and_publish(record)

    async def recover(self, extension_id: str) -> ExtensionRecord:
        """Restart the worker of a persisted ENABLED record after a host restart."""

        record = await self._require(extension_id)
        _require_state(record, {ExtensionState.ENABLED}, "recover")
        return await self._start_and_publish(record)

    async def _start_and_publish(self, record: ExtensionRecord) -> ExtensionRecord:
        extension_id = record.manifest.id
        starting = replace(record, state=ExtensionState.STARTING)
        await self._store.save(starting)
        published = False
        try:
            await self._supervisor.start(starting)
            if not await self._supervisor.health(starting):
                raise ExtensionOperationError(
                    "HEALTHCHECK_FAILED", "extension healthcheck failed"
                )
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
            await shield_cleanup(self._supervisor.stop(starting))
            quarantined = replace(starting, state=ExtensionState.QUARANTINED)
            await shield_cleanup(self._store.save(quarantined))
            raise

    async def disable(self, extension_id: str, *, deadline_epoch: float) -> ExtensionRecord:
        record = await self._require(extension_id)
        _require_state(record, {ExtensionState.ENABLED}, "disable")
        # Revoke new dispatch first.  Runs holding an older snapshot may finish only
        # through the supervisor's bounded drain protocol.
        self._registry.disable(extension_id)
        draining = replace(record, state=ExtensionState.DRAINING)
        await self._store.save(draining)
        drain_report: Mapping[str, object] | None = None
        drain_error: ExtensionError | None = None
        try:
            drain_report = await self._supervisor.drain(draining, deadline_epoch)
        except RpcTimeoutError as exc:
            drain_error = ExtensionOperationError(
                "DRAIN_TIMEOUT", f"extension did not drain before the deadline: {exc}"
            )
        except ExtensionError as exc:
            drain_error = exc
        finally:
            # The physical stop must complete even under cancellation; a version
            # left half-draining may not stay published or running.
            await shield_cleanup(self._supervisor.stop(draining))
        disabled = replace(draining, state=ExtensionState.DISABLED)
        await shield_cleanup(self._store.save(disabled))
        if drain_report is not None:
            drained = drain_report.get("drained")
            active = drain_report.get("active_calls")
            if drained is False or (isinstance(active, int) and active > 0):
                drain_error = ExtensionOperationError(
                    "DRAIN_TIMEOUT",
                    "extension reported in-flight calls when the drain deadline expired",
                )
        if drain_error is not None:
            # The extension is disabled and the worker stopped; the explicit
            # outcome for the timed-out in-flight calls is surfaced to the caller.
            raise drain_error
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
        original = current
        was_enabled = original.state is ExtensionState.ENABLED
        try:
            # Disabling, marking UPGRADING, activating the candidate, persisting
            # it, starting it, health-checking and publishing all live inside the
            # same compensation boundary.
            if was_enabled:
                current = await self.disable(
                    extension_id, deadline_epoch=self._clock().timestamp() + 30
                )
            upgrading = replace(current, state=ExtensionState.UPGRADING)
            await self._store.save(upgrading)
            await self._version_operator.activate(upgrading, candidate)
            activated = replace(candidate, state=ExtensionState.DISABLED)
            await self._store.save(activated)
            if was_enabled:
                return await self.enable(extension_id)
            return activated
        except BaseException:
            await shield_cleanup(
                self._compensate_failed_upgrade(original, candidate, was_enabled)
            )
            raise

    async def _compensate_failed_upgrade(
        self,
        original: ExtensionRecord,
        candidate: ExtensionRecord,
        was_enabled: bool,
    ) -> None:
        """Restore the original version after any upgrade failure or cancel."""

        extension_id = original.manifest.id
        # Never leave candidate capabilities published or its worker running.
        with contextlib.suppress(Exception):
            self._registry.disable(extension_id)
        with contextlib.suppress(Exception):
            await self._supervisor.stop(candidate)
        with contextlib.suppress(Exception):
            await self._store.save(replace(original, state=ExtensionState.ROLLED_BACK))
        if was_enabled:
            # enable() re-runs start + health + publish for the old version and
            # quarantines it if that fails; the original error still propagates.
            with contextlib.suppress(Exception):
                await self.enable(extension_id)

    async def rollback(self, extension_id: str) -> ExtensionRecord:
        original = await self._require(extension_id)
        _require_state(
            original,
            {ExtensionState.ENABLED, ExtensionState.DISABLED, ExtensionState.QUARANTINED},
            "rollback",
        )
        # Resolve and validate the retained candidate while the current version
        # still serves: a missing or incompatible target must never take the
        # running version down.
        restored = await self._version_operator.rollback(original)
        if restored.manifest.id != extension_id:
            raise ExtensionError("rollback restored a different extension id")
        was_enabled = original.state is ExtensionState.ENABLED
        try:
            # Disabling the current version and starting, health-checking,
            # publishing and persisting the restored version all live inside
            # the same compensation boundary: if the restored version cannot
            # serve, the pre-rollback version is brought back instead.
            if was_enabled:
                await self.disable(
                    extension_id, deadline_epoch=self._clock().timestamp() + 30
                )
            rolled_back = replace(restored, state=ExtensionState.ROLLED_BACK)
            await self._store.save(rolled_back)
            if was_enabled:
                return await self._start_and_publish(rolled_back)
            return rolled_back
        except BaseException:
            await shield_cleanup(
                self._compensate_failed_rollback(original, restored, was_enabled)
            )
            raise

    async def _compensate_failed_rollback(
        self,
        original: ExtensionRecord,
        restored: ExtensionRecord,
        was_enabled: bool,
    ) -> None:
        """Restore the pre-rollback version after a failed switch."""

        extension_id = original.manifest.id
        # Never leave the restored version published or its worker running.
        with contextlib.suppress(Exception):
            self._registry.disable(extension_id)
        with contextlib.suppress(Exception):
            await self._supervisor.stop(restored)
        with contextlib.suppress(Exception):
            await self._store.save(replace(original, state=ExtensionState.DISABLED))
        if was_enabled:
            # enable() restarts the original version, health-checks and
            # republishes it; failures quarantine instead of disappearing.
            with contextlib.suppress(Exception):
                await self.enable(extension_id)

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
    mode: str = "install",
    replaces_version: str | None = None,
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
        "mode": mode,
        "replaces_version": replaces_version,
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
        mode=mode,
        replaces_version=replaces_version,
    )


def _semver_key(version: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


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
