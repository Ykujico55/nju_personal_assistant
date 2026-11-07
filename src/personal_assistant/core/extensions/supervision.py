"""Extension Supervisor service: one state machine for API, CLI and recovery.

The service composes the existing confirmation barrier (``InstallCoordinator``),
lifecycle coordinator (``LifecycleManager``) and registry with durable operation
records.  Every mutation returns an ``ExtensionOperation`` id that the Local Admin
API and ``assistantctl`` poll; nothing here is specific to a business extension.

``venv`` and worker processes provide dependency and crash isolation only.  An
installed extension is trusted code running as the same operating-system user.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .async_utils import shield_cleanup
from .errors import (
    ConfirmationRequiredError,
    ExtensionOperationError,
    InvalidLifecycleTransition,
    ManifestValidationError,
    RpcCallError,
    RpcTimeoutError,
)
from .lifecycle import (
    InstallationConfirmation,
    InstallationPreview,
    InstallCoordinator,
    LifecycleManager,
    LifecycleStore,
    RuntimeSupervisor,
)
from .models import ExtensionRecord, ExtensionState
from .operations import (
    DIAGNOSTIC_CODES,
    TERMINAL_OPERATION_STATES,
    ExtensionOperation,
    ExtensionOperationStore,
    OperationState,
)
from .registry import ExtensionRegistry

_STREAM_ERROR_CODES = frozenset({-32090, -32091, -32092, -32093, -32700})

INSTALL_REQUEST_SCOPE = "admin:extensions:install"


def _extension_request_scope(extension_id: str, operation: str) -> str:
    return f"admin:extensions:{extension_id}:{operation}"


def _command_material(fields: Mapping[str, object]) -> str:
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _command_fingerprint(request_scope: str, material: str) -> str:
    payload = {"scope": request_scope, "material": material}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_TRANSIENT_MAPPING = {
    ExtensionState.DISCOVERED: ExtensionState.REJECTED,
    ExtensionState.STAGED: ExtensionState.REJECTED,
    ExtensionState.STARTING: ExtensionState.QUARANTINED,
    ExtensionState.DRAINING: ExtensionState.DISABLED,
    ExtensionState.UPGRADING: ExtensionState.DISABLED,
}


class ExtensionInvoker(Protocol):
    """Host-side dispatch into a running extension worker."""

    async def invoke_tool(
        self,
        extension_id: str,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        task_id: str,
        run_id: str,
        idempotency_key: str,
        deadline_seconds: float,
    ) -> Mapping[str, Any]: ...

    async def stop_all(self) -> None: ...


class ExtensionRuntime(RuntimeSupervisor, ExtensionInvoker, Protocol):
    """Runtime ports required by the supervisor service."""

    async def list_forms(self, extension_id: str) -> list[Mapping[str, Any]]: ...

    async def call_slot(
        self, extension_id: str, method: str, parameters: Mapping[str, Any]
    ) -> Any: ...


class ExtensionSupervisorService:
    def __init__(
        self,
        *,
        coordinator: InstallCoordinator,
        manager: LifecycleManager,
        registry: ExtensionRegistry,
        store: LifecycleStore,
        operations: ExtensionOperationStore,
        runtime: ExtensionRuntime,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._manager = manager
        self._registry = registry
        self._store = store
        self._operations = operations
        self._runtime = runtime
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tasks: set[asyncio.Task[None]] = set()
        # One mutation per extension at a time; the check-and-add is atomic on
        # the event loop, so a second command cannot interleave with the first.
        self._active: set[str] = set()

    # ------------------------------------------------------------------ staging

    async def inspect(self, source: str) -> InstallationPreview:
        """Stage and statically inspect an artifact.  Never runs its code."""

        async with self._store.operation_lock():
            return await self._coordinator.prepare_auto(source)

    async def reject_plan(self, plan_id: str) -> ExtensionRecord:
        async with self._store.operation_lock():
            return await self._coordinator.reject(plan_id)

    # -------------------------------------------------------------- operations

    async def begin_install(
        self,
        plan_id: str,
        confirmation: InstallationConfirmation,
        *,
        idempotency_key: str | None = None,
    ) -> ExtensionOperation:
        # Replay is resolved from durable state before the process-local plan is
        # touched, so it survives an Admin restart.
        fingerprint = _command_fingerprint(
            INSTALL_REQUEST_SCOPE,
            _command_material(
                {
                    "plan_id": plan_id,
                    "confirmation_nonce": confirmation.confirmation_nonce,
                    "preview_hash": confirmation.preview_hash,
                    "accepted_warning": confirmation.accepted_warning,
                }
            ),
        )
        replay = await self._replay_or_none(INSTALL_REQUEST_SCOPE, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        preview = self._coordinator.preview_for(plan_id)
        if preview.mode != "install":
            raise ExtensionOperationError("PLAN_MODE_MISMATCH", "plan is an upgrade candidate")
        self._coordinator.validate(plan_id, confirmation)
        return await self._begin(
            preview.extension_id,
            "install",
            lambda: self._coordinator.install(plan_id, confirmation),
            request_scope=INSTALL_REQUEST_SCOPE,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )

    async def begin_upgrade(
        self,
        extension_id: str,
        plan_id: str,
        confirmation: InstallationConfirmation,
        *,
        idempotency_key: str | None = None,
    ) -> ExtensionOperation:
        request_scope = _extension_request_scope(extension_id, "upgrade")
        fingerprint = _command_fingerprint(
            request_scope,
            _command_material(
                {
                    "extension_id": extension_id,
                    "plan_id": plan_id,
                    "confirmation_nonce": confirmation.confirmation_nonce,
                    "preview_hash": confirmation.preview_hash,
                    "accepted_warning": confirmation.accepted_warning,
                }
            ),
        )
        replay = await self._replay_or_none(request_scope, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        preview = self._coordinator.preview_for(plan_id)
        if preview.mode != "upgrade" or preview.extension_id != extension_id:
            raise ExtensionOperationError(
                "PLAN_MODE_MISMATCH", "plan does not upgrade this extension"
            )
        self._coordinator.validate(plan_id, confirmation)

        async def work() -> None:
            candidate = await self._coordinator.install_candidate(plan_id, confirmation)
            try:
                await self._manager.upgrade(extension_id, candidate)
            except BaseException:
                # Best-effort cleanup of the failed candidate; the old version stays.
                with contextlib.suppress(Exception):
                    await self._coordinator.discard_candidate(candidate)
                raise

        return await self._begin(
            extension_id,
            "upgrade",
            work,
            request_scope=request_scope,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )

    async def begin_enable(
        self, extension_id: str, *, idempotency_key: str | None = None
    ) -> ExtensionOperation:
        scope = _extension_request_scope(extension_id, "enable")
        fingerprint = _command_fingerprint(scope, "")
        # Replays describe the original command, even if the extension was
        # subsequently disabled or uninstalled. Validate only fresh commands.
        replay = await self._replay_or_none(scope, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        record = await self._store.get(extension_id)
        if record is None or record.state in {
            ExtensionState.DISCOVERED,
            ExtensionState.STAGED,
            ExtensionState.REJECTED,
            ExtensionState.UNINSTALLED,
        }:
            raise ExtensionOperationError(
                "EXTENSION_NOT_INSTALLED",
                "扩展尚未安装。请先选择源码目录，预览并确认安装材料，安装成功后再启用。",
            )
        if not record.install_path:
            raise ExtensionOperationError(
                "INSTALL_PATH_MISSING", "扩展安装记录缺少运行目录，请先核对安装状态。"
            )
        return await self._begin(
            extension_id,
            "enable",
            lambda: self._manager.enable(extension_id),
            request_scope=scope,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )

    async def begin_disable(
        self,
        extension_id: str,
        *,
        drain_seconds: float = 30.0,
        idempotency_key: str | None = None,
    ) -> ExtensionOperation:
        deadline = self._clock().timestamp() + max(1.0, drain_seconds)
        scope = _extension_request_scope(extension_id, "disable")
        return await self._begin(
            extension_id,
            "disable",
            lambda: self._manager.disable(extension_id, deadline_epoch=deadline),
            request_scope=scope,
            idempotency_key=idempotency_key,
            fingerprint=_command_fingerprint(
                scope, _command_material({"drain_seconds": drain_seconds})
            ),
        )

    async def begin_rollback(
        self, extension_id: str, *, idempotency_key: str | None = None
    ) -> ExtensionOperation:
        async def work() -> None:
            current = await self._store.get(extension_id)
            if current is None:
                raise ExtensionOperationError(
                    "EXTENSION_NOT_FOUND", f"unknown extension: {extension_id}"
                )
            # rollback() owns the whole switch, including re-enabling the
            # restored version, so a failure there cannot leave the extension
            # without a serving version.
            await self._manager.rollback(extension_id)

        scope = _extension_request_scope(extension_id, "rollback")
        return await self._begin(
            extension_id,
            "rollback",
            work,
            request_scope=scope,
            idempotency_key=idempotency_key,
            fingerprint=_command_fingerprint(scope, ""),
        )

    async def begin_uninstall(
        self, extension_id: str, *, idempotency_key: str | None = None
    ) -> ExtensionOperation:
        scope = _extension_request_scope(extension_id, "uninstall")
        return await self._begin(
            extension_id,
            "uninstall",
            lambda: self._manager.uninstall(extension_id),
            request_scope=scope,
            idempotency_key=idempotency_key,
            fingerprint=_command_fingerprint(scope, ""),
        )

    async def operation(self, operation_id: str) -> ExtensionOperation | None:
        return await self._operations.get(operation_id)

    async def wait_operation(
        self, operation_id: str, *, timeout_seconds: float = 120.0
    ) -> ExtensionOperation:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            operation = await self._operations.get(operation_id)
            if operation is None:
                raise ExtensionOperationError(
                    "OPERATION_NOT_FOUND", f"unknown operation: {operation_id}"
                )
            if operation.status in TERMINAL_OPERATION_STATES:
                return operation
            await asyncio.sleep(0.05)
        raise TimeoutError(f"operation {operation_id} did not finish in time")

    # -------------------------------------------------------------------- state

    async def records(self) -> tuple[ExtensionRecord, ...]:
        return tuple(await self._store.all())

    async def record(self, extension_id: str) -> ExtensionRecord | None:
        return await self._store.get(extension_id)

    async def list_forms(self) -> tuple[dict[str, Any], ...]:
        """Read declared form descriptors from enabled Workers, never executable UI."""

        forms: list[dict[str, Any]] = []
        for record in await self._store.all():
            if record.state is not ExtensionState.ENABLED or record.manifest is None:
                continue
            declared = set(record.manifest.forms)
            if not declared:
                continue
            # The public API may run in a separate process from Admin.  An
            # ENABLED record already represents the user's install/enable
            # authorization; start that exact installed version on demand.
            await self._runtime.start(record)
            returned = await self._runtime.list_forms(record.manifest.id)
            if {item.get("id") for item in returned} != declared:
                raise ExtensionOperationError(
                    "FORM_SCHEMA_DRIFT", "form list differs from manifest"
                )
            for item in returned:
                if not isinstance(item.get("json_schema"), dict) or not isinstance(
                    item.get("ui_schema", {}), dict
                ):
                    raise ExtensionOperationError(
                        "FORM_SCHEMA_DRIFT", "form descriptor is incomplete"
                    )
                forms.append(
                    {
                        "extension_id": record.manifest.id,
                        "extension_version": record.manifest.version,
                        "id": item["id"],
                        "json_schema": item["json_schema"],
                        "ui_schema": item.get("ui_schema", {}),
                    }
                )
        return tuple(forms)

    async def list_context_providers(self) -> tuple[dict[str, Any], ...]:
        """Instance catalog without starting code or revealing source configuration."""
        return tuple(
            {"extension_id": record.manifest.id,
             "extension_version": record.manifest.version,
             "name": record.manifest.name,
             "provider_ids": list(record.manifest.context_providers),
             "provider_descriptions": dict(record.manifest.context_provider_descriptions)}
            for record in await self._store.all()
            if record.state is ExtensionState.ENABLED and record.manifest.context_providers
        )

    async def retrieve_context(
        self, extension_id: str, extension_version: str, *, query: str, limit: int,
        provider_ids: tuple[str, ...] = (),
    ) -> tuple[dict[str, Any], ...]:
        """Read a declared ContextProvider, never invoke a tool or create a task."""
        if (not isinstance(query, str) or not query.strip() or len(query) > 2000
                or type(limit) is not int or not 1 <= limit <= 10
                or not isinstance(provider_ids, (list, tuple)) or not 1 <= len(provider_ids) <= 8
                or any(not isinstance(p, str) for p in provider_ids)):
            raise ExtensionOperationError("CONTEXT_REQUEST_INVALID", "invalid context request")
        record = await self._store.get(extension_id)
        if (record is None or record.state is not ExtensionState.ENABLED
                or record.manifest.version != extension_version
                or not record.manifest.context_providers):
            raise ExtensionOperationError("EXTENSION_NOT_ENABLED", "context provider unavailable")
        if not set(provider_ids) <= set(record.manifest.context_providers):
            raise ExtensionOperationError("CONTEXT_REQUEST_INVALID", "invalid context selection")
        selected = list(dict.fromkeys(provider_ids))
        # Legacy single-provider implementations may reject unknown filters.
        # Their selection is unambiguous, so preserve the old query shape.
        filters = {"provider_ids": selected} if len(record.manifest.context_providers) > 1 else {}
        try:
            await self._runtime.start(record)
            result = await self._runtime.call_slot(
                extension_id, "context.retrieve",
                {"query": {"text": query, "limit": limit,
                           "filters": filters}},
            )
            current = await self._store.get(extension_id)
            if current != record:
                raise ValueError("context provider changed during retrieval")
            if (not isinstance(result, list) or len(result) > 100
                    or not all(isinstance(item, dict) for item in result)):
                raise ValueError("invalid context response")
            return tuple(result)
        except Exception:
            # Provider diagnostics may contain paths/body; only a fixed code is
            # exposed to the conversation layer. Cancellation still propagates.
            raise ExtensionOperationError(
                "CONTEXT_PROVIDER_UNAVAILABLE", "context provider unavailable"
            ) from None

    async def list_declarations(self, method: str) -> tuple[dict[str, Any], ...]:
        slots = {
            "workflow.list": "workflows",
            "schedule.list": "schedules",
            "event_source.list": "event_sources",
        }
        attribute = slots.get(method)
        if attribute is None:
            raise ExtensionOperationError("CAPABILITY_NOT_PUBLISHED", "unsupported enumeration")
        result: list[dict[str, Any]] = []
        for record in await self._store.all():
            if record.state is not ExtensionState.ENABLED or record.manifest is None:
                continue
            declared = set(getattr(record.manifest, attribute))
            if not declared:
                continue
            await self._runtime.start(record)
            returned = await self._runtime.call_slot(record.manifest.id, method, {})
            if (
                not isinstance(returned, list)
                or not all(isinstance(item, dict) for item in returned)
                or len(returned) != len(declared)
                or {item.get("id") for item in returned} != declared
            ):
                raise ExtensionOperationError(
                    "MANIFEST_INVALID", "declarations differ from manifest"
                )
            result.extend(
                {
                    **item,
                    "extension_id": record.manifest.id,
                    "extension_version": record.manifest.version,
                }
                for item in returned
            )
        return tuple(result)

    async def poll_source(
        self, extension_id: str, extension_version: str, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        record = await self._store.get(extension_id)
        if (
            record is None
            or record.state is not ExtensionState.ENABLED
            or record.manifest.version != extension_version
            or request.get("source_id") not in record.manifest.event_sources
        ):
            raise ExtensionOperationError(
                "EXTENSION_NOT_ENABLED", "event source/version unavailable"
            )
        await self._runtime.start(record)
        result = await self._runtime.call_slot(
            extension_id, "event_source.poll", {"request": request}
        )
        if not isinstance(result, dict):
            raise ExtensionOperationError("MANIFEST_INVALID", "event poll result must be an object")
        declarations = await self._runtime.call_slot(extension_id, "event_source.list", {})
        source = (
            next(
                (
                    s
                    for s in declarations
                    if isinstance(s, dict) and s.get("id") == request.get("source_id")
                ),
                None,
            )
            if isinstance(declarations, list)
            else None
        )
        if (
            source is None
            or not isinstance(result.get("events"), list)
            or any(
                not isinstance(e, dict) or e.get("type") not in source.get("event_types", [])
                for e in result["events"]
            )
        ):
            raise ExtensionOperationError(
                "MANIFEST_INVALID", "event type differs from the declared source"
            )
        return result

    async def invoke_tool(
        self,
        extension_id: str,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        task_id: str,
        run_id: str,
        idempotency_key: str,
        deadline_seconds: float = 30.0,
    ) -> Mapping[str, Any]:
        record = await self._store.get(extension_id)
        if record is None or record.state is not ExtensionState.ENABLED:
            raise ExtensionOperationError(
                "EXTENSION_NOT_ENABLED", f"extension is not enabled: {extension_id}"
            )
        owner = self._registry.snapshot.owner_of(tool_id)
        if (
            owner is None
            or owner.extension_id != extension_id
            or owner.extension_version != record.manifest.version
        ):
            raise ExtensionOperationError(
                "CAPABILITY_NOT_PUBLISHED", f"capability is not published: {tool_id}"
            )
        try:
            return await self._runtime.invoke_tool(
                extension_id,
                tool_id,
                arguments,
                task_id=task_id,
                run_id=run_id,
                idempotency_key=idempotency_key,
                deadline_seconds=deadline_seconds,
            )
        except (RpcCallError, RpcTimeoutError) as exc:
            if isinstance(exc, RpcTimeoutError) or exc.code in _STREAM_ERROR_CODES:
                await self._quarantine(record, exc)
            raise

    # ----------------------------------------------------------------- recovery

    async def recover(self) -> None:
        """Restore persisted state after a host restart.

        Workers do not survive the host process, so persisting ENABLED means
        "start it again".  Transient states are resolved to a safe terminal state,
        interrupted operations are marked FAILED, and the registry is republished
        exactly once with the extension records that came back healthy.
        """

        async with self._store.operation_lock():
            await self._operations.interrupt_running(diagnostic_code="SUPERVISOR_RESTART")
            enabled: list[ExtensionRecord] = []
            for record in await self._store.all():
                try:
                    recovered = await self._recover_record(record)
                except Exception:  # noqa: BLE001 - one broken extension must not block others
                    with contextlib.suppress(Exception):
                        await asyncio.shield(self._runtime.stop(record))
                    recovered = replace(record, state=ExtensionState.QUARANTINED)
                    await self._store.save(recovered)
                    await self._record_operation(
                        record.manifest.id, "recover", OperationState.FAILED, "RECOVERY_FAILED"
                    )
                if recovered.state is ExtensionState.ENABLED:
                    enabled.append(recovered)
            self._registry.replace_all(enabled)

    async def _recover_record(self, record: ExtensionRecord) -> ExtensionRecord:
        state = record.state
        if state is ExtensionState.ENABLED:
            if not record.install_path:
                quarantined = replace(record, state=ExtensionState.QUARANTINED)
                await self._store.save(quarantined)
                await self._record_operation(
                    record.manifest.id,
                    "recover",
                    OperationState.FAILED,
                    "INSTALL_PATH_MISSING",
                )
                return quarantined
            starting = replace(record, state=ExtensionState.STARTING)
            await self._store.save(starting)
            try:
                await self._runtime.start(starting)
                if not await self._runtime.health(starting):
                    raise ExtensionOperationError(
                        "HEALTHCHECK_FAILED", "extension healthcheck failed after restart"
                    )
            except asyncio.CancelledError:
                # Keep STARTING as an interrupted recovery for the next owner,
                # but never leave this process's newly started worker untracked.
                with contextlib.suppress(Exception):
                    await shield_cleanup(self._runtime.stop(starting))
                raise
            except Exception as exc:  # noqa: BLE001 - quarantine and continue
                return await self._quarantine_recovered(record, starting, exc)
            enabled = replace(starting, state=ExtensionState.ENABLED)
            try:
                await self._store.save(enabled)
            except Exception as exc:  # noqa: BLE001 - stop the worker before quarantine
                return await self._quarantine_recovered(record, starting, exc)
            return enabled
        if state is ExtensionState.UNINSTALLING:
            installed = record.install_path is not None and Path(record.install_path).is_dir()
            if installed:
                mapped = replace(record, state=ExtensionState.DISABLED)
            else:
                mapped = replace(
                    record,
                    state=ExtensionState.UNINSTALLED,
                    install_path=None,
                    tombstone=True,
                    data_retained=True,
                )
            await self._store.save(mapped)
            return mapped
        if state in _TRANSIENT_MAPPING:
            mapped = replace(record, state=_TRANSIENT_MAPPING[state])
            if state is ExtensionState.STAGED:
                # An unconfirmed staging tree must not outlive the process.
                await self._coordinator.discard_staged_record(record)
            await self._store.save(mapped)
            return mapped
        return record

    async def _quarantine_recovered(
        self,
        record: ExtensionRecord,
        starting: ExtensionRecord,
        exc: Exception,
    ) -> ExtensionRecord:
        """Stop a worker whose recovery failed, then persist QUARANTINED."""

        with contextlib.suppress(Exception):
            await asyncio.shield(self._runtime.stop(starting))
        quarantined = replace(starting, state=ExtensionState.QUARANTINED)
        with contextlib.suppress(Exception):
            await self._store.save(quarantined)
        await self._record_operation(
            record.manifest.id,
            "recover",
            OperationState.FAILED,
            self._diagnostic("recover", exc),
        )
        return quarantined

    async def synchronize_configurations(self) -> None:
        synchronize = getattr(self._runtime, "synchronize_configurations", None)
        if synchronize is not None:
            await synchronize()

    async def stop_all(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._runtime.stop_all()

    # ------------------------------------------------------------------ helpers

    async def _replay_or_none(
        self,
        request_scope: str,
        idempotency_key: str | None,
        fingerprint: str,
    ) -> ExtensionOperation | None:
        if not idempotency_key:
            return None
        existing = await self._operations.find_by_request_scope(request_scope, idempotency_key)
        if existing is None:
            return None
        if existing.command_fingerprint == fingerprint:
            return existing
        raise ExtensionOperationError(
            "IDEMPOTENCY_CONFLICT",
            "the idempotency key was reused with different input",
        )

    async def _begin(
        self,
        extension_id: str,
        kind: str,
        work: Callable[[], Awaitable[object]],
        *,
        request_scope: str,
        idempotency_key: str | None = None,
        fingerprint: str | None = None,
    ) -> ExtensionOperation:
        fingerprint = fingerprint or _command_fingerprint(request_scope, "")
        replay = await self._replay_or_none(request_scope, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        if extension_id in self._active:
            raise ExtensionOperationError(
                "OPERATION_IN_PROGRESS",
                f"another lifecycle operation is running for {extension_id}",
            )
        self._active.add(extension_id)
        now = self._clock()
        operation = ExtensionOperation(
            id=f"extop_{uuid.uuid4().hex}",
            extension_id=extension_id,
            operation=kind,
            status=OperationState.PENDING,
            idempotency_key=idempotency_key,
            command_fingerprint=fingerprint,
            request_scope=request_scope,
            created_at=now,
            updated_at=now,
        )
        ready: asyncio.Future[ExtensionOperation] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self._claim_and_execute(operation, work, ready))
        self._tasks.add(task)
        task.add_done_callback(self._operation_done)
        try:
            return await asyncio.shield(ready)
        except asyncio.CancelledError:
            # Before the durable receipt exists, cancellation must remove the
            # waiter rather than let it acquire the gate and execute later.
            # Once published, the accepted background command owns its lifetime.
            if not ready.done():
                task.cancel()
                await shield_cleanup(asyncio.gather(task, return_exceptions=True))
            if ready.done() and not ready.cancelled():
                ready.exception()
            raise

    def _operation_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        # Failures are persisted by the runner before they can leave this task.
        # Retrieve the exception also when the HTTP requester has disconnected.
        if not task.cancelled():
            task.exception()

    async def _claim_and_execute(
        self, operation: ExtensionOperation, work: Callable[[], Awaitable[object]],
        ready: asyncio.Future[ExtensionOperation],
    ) -> None:
        persisted = False
        try:
            async with self._store.operation_lock():
                # Re-check replay after crossing the cross-process gate. The
                # PENDING receipt and its execution share this same ownership.
                replay = await self._replay_or_none(
                    operation.request_scope or "", operation.idempotency_key,
                    operation.command_fingerprint or "",
                )
                if replay is not None:
                    ready.set_result(replay)
                    return
                await self._operations.create(operation)
                persisted = True
                ready.set_result(operation)
                await self._execute(operation.id, operation.extension_id, operation.operation, work)
        except BaseException as exc:
            # A failed durable create may have committed before cancellation.
            # Do not execute it later or leave an unhandled task exception.
            if persisted or isinstance(exc, asyncio.CancelledError):
                with contextlib.suppress(Exception):
                    await shield_cleanup(self._operations.update(
                        operation.id, status=OperationState.FAILED,
                        diagnostic_code=("OPERATION_CANCELLED" if isinstance(
                            exc, asyncio.CancelledError
                        ) else "OPERATION_FAILED"),
                    ))
            if not ready.done():
                if isinstance(exc, asyncio.CancelledError):
                    ready.cancel()
                else:
                    ready.set_exception(exc)
            raise
        finally:
            self._active.discard(operation.extension_id)

    async def _execute(
        self,
        operation_id: str,
        extension_id: str,
        kind: str,
        work: Callable[[], Awaitable[object]],
    ) -> None:
        try:
            await self._operations.update(operation_id, status=OperationState.RUNNING)
        except Exception:  # noqa: BLE001 - never leave a task-less PENDING row
            with contextlib.suppress(Exception):
                await self._operations.update(
                    operation_id,
                    status=OperationState.FAILED,
                    diagnostic_code="OPERATION_FAILED",
                )
            return
        try:
            await work()
        except asyncio.CancelledError:
            await self._operations.update(
                operation_id,
                status=OperationState.FAILED,
                diagnostic_code="OPERATION_CANCELLED",
            )
            raise
        except Exception as exc:  # noqa: BLE001 - boundary maps to a safe diagnostic code
            await self._operations.update(
                operation_id,
                status=OperationState.FAILED,
                diagnostic_code=self._diagnostic(kind, exc),
            )
        else:
            await self._operations.update(operation_id, status=OperationState.SUCCEEDED)

    async def _quarantine(self, record: ExtensionRecord, exc: Exception) -> None:
        extension_id = record.manifest.id
        # Verify the durable owner *before* touching the registry: a late
        # failure from an old worker must never remove a newer version from the
        # registry while the database still reports it as ENABLED.
        current = await self._store.get(extension_id)
        if (
            current is None
            or current.state is not ExtensionState.ENABLED
            or current.manifest.version != record.manifest.version
        ):
            return
        self._registry.disable(extension_id)
        # Guard the check/disable window against a concurrent upgrade: if the
        # durable owner changed in between, restore its registration.
        after = await self._store.get(extension_id)
        if (
            after is None
            or after.state is not ExtensionState.ENABLED
            or after.manifest.version != record.manifest.version
        ):
            if after is not None and after.state is ExtensionState.ENABLED:
                self._registry.enable(after)
            return
        # Atomic compare-and-set: only the exact durable state observed above
        # may be quarantined.  A concurrent disable/update wins and is kept.
        applied = await self._store.save(
            replace(after, state=ExtensionState.QUARANTINED),
            expected=(ExtensionState.ENABLED, after.manifest.version),
        )
        # ``applied is False`` is an explicit CAS failure; stores that do not
        # implement CAS return None and are treated as having written.
        if applied is False:
            fresh = await self._store.get(extension_id)
            if fresh is not None and fresh.state is ExtensionState.ENABLED:
                self._registry.enable(fresh)
            return
        await self._record_operation(
            record.manifest.id,
            "quarantine",
            OperationState.FAILED,
            self._diagnostic("quarantine", exc),
        )

    async def _record_operation(
        self,
        extension_id: str,
        kind: str,
        status: OperationState,
        diagnostic_code: str | None,
    ) -> None:
        now = self._clock()
        with contextlib.suppress(Exception):
            await self._operations.create(
                ExtensionOperation(
                    id=f"extop_{uuid.uuid4().hex}",
                    extension_id=extension_id,
                    operation=kind,
                    status=status,
                    diagnostic_code=diagnostic_code,
                    created_at=now,
                    updated_at=now,
                )
            )

    def _diagnostic(self, kind: str, exc: BaseException) -> str:
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code in DIAGNOSTIC_CODES:
            return code
        if isinstance(exc, RpcTimeoutError):
            return "WORKER_TIMEOUT"
        if isinstance(exc, RpcCallError):
            return "HANDSHAKE_MISMATCH" if exc.code == -32095 else "WORKER_CRASHED"
        if isinstance(exc, ConfirmationRequiredError):
            return "CONFIRMATION_REQUIRED"
        if isinstance(exc, ManifestValidationError):
            return "MANIFEST_INVALID"
        if isinstance(exc, InvalidLifecycleTransition):
            return "STATE_CONFLICT"
        candidate = f"{kind.upper()}_FAILED"
        if candidate in DIAGNOSTIC_CODES:
            return candidate
        return "OPERATION_FAILED"
