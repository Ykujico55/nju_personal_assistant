"""Real worker-process supervision over newline-delimited JSON-RPC 2.0.

Each extension version runs in its own subprocess started from that version's
venv.  A broken stream, timeout, oversized frame or unexpected exit stops the
process and surfaces a typed error; nothing is silently retried here.  The venv
and process isolate dependencies and crashes, not malicious code.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from personal_assistant.core.extensions.artifact_access import (
    CAPABILITY_ARTIFACT_READ,
    CAPABILITY_ARTIFACT_WRITE,
    HOST_ARTIFACT_METHODS,
    ExtensionArtifactAccess,
    ExtensionArtifactContext,
)
from personal_assistant.core.extensions.async_utils import shield_cleanup
from personal_assistant.core.extensions.config import (
    ExtensionConfigApplyStore,
    ExtensionConfigStore,
    validate_extension_config,
)
from personal_assistant.core.extensions.data_access import (
    CAPABILITY_EXTENSION_DATA,
    HOST_DATA_METHODS,
    ExtensionDataAccess,
    ExtensionDataContext,
)
from personal_assistant.core.extensions.errors import (
    ExtensionOperationError,
    RpcCallError,
    RpcTimeoutError,
)
from personal_assistant.core.extensions.lifecycle import InstalledArtifact
from personal_assistant.core.extensions.manifest import ExtensionManifest
from personal_assistant.core.extensions.models import (
    ExtensionRecord,
    ExtensionState,
    data_namespace,
)
from personal_assistant.core.extensions.rpc import (
    WORKER_INPUT_FRAME_BYTES,
    ExtensionWorker,
    HostRequestHandler,
    JsonRpcProcessClient,
    WorkerSpec,
)
from personal_assistant.infrastructure.browser.host import (
    HOST_BROWSER_METHODS,
    BrowserCapabilityContext,
    BrowserHostCapability,
)
from personal_assistant.infrastructure.mail.host import (
    HOST_MAIL_METHODS,
    MailCapabilityContext,
    MailHostCapability,
)

from .config_store import load_extension_config_schema
from .installer import venv_python

MAIL_READ_CAPABILITY = "mail.read"
MAIL_SEND_CAPABILITY = "mail.send"
BROWSER_READ_CAPABILITY = "browser.read"

_ID_SLOT_METHODS = {
    "EventSource": "event_source.list",
    "WorkflowProvider": "workflow.list",
    "ScheduleProvider": "schedule.list",
    "FormSchemaProvider": "form.list",
}


def _payload_root(version_dir: Path) -> Path:
    payload = version_dir / "payload"
    return payload if payload.is_dir() else version_dir


def _build_worker(
    manifest: ExtensionManifest,
    version_dir: Path,
    *,
    max_frame_bytes: int,
    host_handler: HostRequestHandler | None = None,
) -> ExtensionWorker:
    if not version_dir.is_dir():
        raise ExtensionOperationError(
            "INSTALL_PATH_MISSING", "the installed extension directory is missing"
        )
    python = venv_python(version_dir / "venv")
    if not python.is_file():
        raise ExtensionOperationError(
            "INSTALL_PATH_MISSING", "the extension venv interpreter is missing"
        )
    client = JsonRpcProcessClient(
        WorkerSpec(
            module=manifest.module_name,
            python_executable=str(python),
            cwd=str(_payload_root(version_dir)),
            host_handler=host_handler,
        ),
        max_frame_bytes=max_frame_bytes,
        max_outbound_frame_bytes=WORKER_INPUT_FRAME_BYTES,
    )
    return ExtensionWorker(manifest, client)


class ProcessRuntimeSupervisor:
    def __init__(
        self,
        *,
        handshake_timeout_seconds: float = 10.0,
        health_timeout_seconds: float = 5.0,
        drain_timeout_seconds: float = 30.0,
        max_frame_bytes: int = 4 * 1024 * 1024,
        data_access: ExtensionDataAccess | None = None,
        config_store: ExtensionConfigStore | None = None,
        record_reader: Callable[[str], Awaitable[ExtensionRecord | None]] | None = None,
        mail_capability: MailHostCapability | None = None,
        mail_send_available: bool = False,
        artifact_access: ExtensionArtifactAccess | None = None,
        browser_capability: BrowserHostCapability | None = None,
    ) -> None:
        self._handshake_timeout = handshake_timeout_seconds
        self._health_timeout = health_timeout_seconds
        self._drain_timeout = drain_timeout_seconds
        self._max_frame_bytes = max_frame_bytes
        self._data_access = data_access
        self._config_store = config_store
        self._record_reader = record_reader
        self._mail_capability = mail_capability
        self._mail_send_available = mail_send_available
        self._artifact_access = artifact_access
        self._browser_capability = browser_capability
        self._workers: dict[str, ExtensionWorker] = {}
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._records: dict[str, ExtensionRecord] = {}
        self._loaded: dict[str, Mapping[str, Any]] = {}
        self._config_failures: dict[str, str] = {}
        self._instance_id = uuid.uuid4().hex
        self._config_sync_task: asyncio.Task[None] | None = None

    async def start(self, record: ExtensionRecord) -> None:
        extension_id = record.manifest.id
        lock = self._start_locks.setdefault(extension_id, asyncio.Lock())
        async with lock:
            await self._start_locked(
                record,
                allow_starting=record.state is ExtensionState.STARTING,
            )
        if (
            isinstance(self._config_store, ExtensionConfigApplyStore)
            and self._config_sync_task is None
        ):
            self._config_sync_task = asyncio.create_task(self._configuration_loop())

    async def _current_record(
        self,
        record: ExtensionRecord,
        *,
        allow_starting: bool = False,
    ) -> ExtensionRecord:
        if self._record_reader is None:
            return record
        current = await self._record_reader(record.manifest.id)
        allowed = {ExtensionState.ENABLED}
        if allow_starting:
            allowed.add(ExtensionState.STARTING)
        if (
            current is None
            or current.state not in allowed
            or current.manifest.version != record.manifest.version
            or current.artifact_hash != record.artifact_hash
            or current.install_path != record.install_path
        ):
            raise ExtensionOperationError(
                "EXTENSION_NOT_ENABLED",
                "extension lifecycle changed; worker unavailable",
            )
        return current

    async def _start_locked(
        self,
        record: ExtensionRecord,
        *,
        allow_starting: bool = False,
    ) -> None:
        record = await self._current_record(record, allow_starting=allow_starting)
        extension_id = record.manifest.id
        self._require_capabilities(record.manifest)
        self._records[extension_id] = record
        store = self._config_store
        target = (
            await store.get_applied(extension_id)
            if isinstance(store, ExtensionConfigApplyStore)
            else None
        )
        existing = self._workers.get(extension_id)
        loaded = self._loaded.get(extension_id)
        if (
            existing is not None
            and existing.client.running
            and existing.manifest.version == record.manifest.version
            and (
                target is None
                or (loaded is not None and loaded["generation"] == target["generation"])
            )
        ):
            return
        if target is not None and self._config_failures.get(extension_id) == target["generation"]:
            raise ExtensionOperationError("CONFIG_APPLY_FAILED", "configuration is not active")
        config: Mapping[str, Any] = (
            target["config"]
            if target
            else (await store.get(extension_id) if store is not None else {})
        )
        if target is not None:
            await self._report_configuration(extension_id, target, "APPLYING")
        worker: ExtensionWorker | None = None
        try:
            schema = load_extension_config_schema(record.manifest)
            if config:
                config = validate_extension_config(schema, config)
            if existing is not None:
                await existing.close()
                self._workers.pop(extension_id, None)
            worker = _build_worker(
                record.manifest,
                Path(record.install_path or ""),
                max_frame_bytes=self._max_frame_bytes,
                host_handler=self._host_handler(record),
            )
            await worker.start(non_secret_config=config, timeout_seconds=self._handshake_timeout)
            if target is not None:
                health = await worker.health(timeout_seconds=self._health_timeout)
                if not health.get("healthy"):
                    raise ExtensionOperationError(
                        "HEALTHCHECK_FAILED", "configuration health failed"
                    )
            await self._current_record(record, allow_starting=allow_starting)
            if self._records.get(extension_id) is not record:
                raise ExtensionOperationError("EXTENSION_NOT_ENABLED", "worker start was stopped")
            self._workers[extension_id] = worker
            if target is not None:
                self._loaded[extension_id] = target
                self._config_failures.pop(extension_id, None)
                await self._report_configuration(extension_id, target, "RUNNING")
        except BaseException:
            if worker is not None:
                await shield_cleanup(worker.close())
            # An invalid newly applied configuration must not leave old roots callable.
            if existing is not None:
                await shield_cleanup(existing.close())
            self._workers.pop(extension_id, None)
            if target is not None:
                self._config_failures[extension_id] = str(target["generation"])
                await shield_cleanup(
                    self._report_configuration(
                        extension_id,
                        target,
                        "FAILED",
                        "CONFIG_APPLY_FAILED",
                    )
                )
            raise

    async def _report_configuration(
        self,
        extension_id: str,
        target: Mapping[str, Any],
        state: str,
        error_code: str | None = None,
    ) -> None:
        store = self._config_store
        if isinstance(store, ExtensionConfigApplyStore):
            loaded = self._loaded.get(extension_id)
            await store.report_runtime(
                extension_id,
                self._instance_id,
                loaded_digest=str(loaded["digest"]) if loaded else None,
                generation=str(target["generation"]),
                state=state,
                error_code=error_code,
            )

    async def synchronize_configurations(self) -> None:
        if not isinstance(self._config_store, ExtensionConfigApplyStore):
            return
        for extension_id, record in list(self._records.items()):
            lock = self._start_locks.setdefault(extension_id, asyncio.Lock())
            async with lock:
                if self._records.get(extension_id) != record:
                    continue
                try:
                    if self._record_reader is not None:
                        durable = await self._record_reader(extension_id)
                        if durable is not None and durable.state in {
                            ExtensionState.STARTING,
                            ExtensionState.DRAINING,
                            ExtensionState.UPGRADING,
                            ExtensionState.UNINSTALLING,
                        }:
                            # The lifecycle owner controls bounded start/drain.
                            # Configuration sync neither publishes nor stops it.
                            continue
                    try:
                        current = await self._current_record(record)
                    except ExtensionOperationError:
                        # Another host may disable, quarantine or replace this
                        # version. Close our idle replica; never resurrect it.
                        await self.stop(record)
                        continue
                    worker = self._workers.get(extension_id)
                    loaded = self._loaded.get(extension_id)
                    if worker is not None and not worker.client.running:
                        if loaded is not None:
                            self._config_failures[extension_id] = str(loaded["generation"])
                            await self._report_configuration(
                                extension_id,
                                loaded,
                                "FAILED",
                                "CONFIG_UNAVAILABLE",
                            )
                        continue
                    await self._start_locked(current)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Per-replica failure is persisted with a fixed diagnostic;
                    # unchanged failed generations are never retried in this loop.
                    continue

    async def _configuration_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            await self.synchronize_configurations()

    @asynccontextmanager
    async def _configured_worker(
        self,
        extension_id: str,
        *,
        retain_inflight_result: bool = False,
    ) -> AsyncIterator[ExtensionWorker]:
        # The same lock covers a whole original RPC. Configuration application
        # waits for it; it does not cancel or replay an in-flight external write.
        lock = self._start_locks.setdefault(extension_id, asyncio.Lock())
        async with lock:
            record = self._records.get(extension_id)
            existing = self._workers.get(extension_id)
            if existing is not None and not existing.client.running:
                raise RpcCallError(-32090, "extension worker is not running")
            if record is not None:
                await self._start_locked(record)
            worker = self._workers.get(extension_id)
            if worker is None or not worker.client.running:
                raise RpcCallError(-32090, "extension worker is not running")
            yield worker
            if not retain_inflight_result and record is not None:
                await self._current_record(record)
            if not retain_inflight_result and isinstance(
                self._config_store, ExtensionConfigApplyStore
            ):
                current = await self._config_store.get_applied(extension_id)
                loaded = self._loaded.get(extension_id)
                if loaded is None or loaded["generation"] != current["generation"]:
                    raise ExtensionOperationError(
                        "CONFIG_CHANGED",
                        "configuration changed during the read; result discarded",
                    )

    def _capability_available(self, capability: str) -> bool:
        if capability == CAPABILITY_EXTENSION_DATA:
            return self._data_access is not None and self._data_access.available
        if capability in {CAPABILITY_ARTIFACT_READ, CAPABILITY_ARTIFACT_WRITE}:
            return self._artifact_access is not None and self._artifact_access.available
        if capability == MAIL_READ_CAPABILITY:
            return self._mail_capability is not None and self._mail_capability.available
        if capability == MAIL_SEND_CAPABILITY:
            return self._mail_send_available
        if capability == BROWSER_READ_CAPABILITY:
            return self._browser_capability is not None and self._browser_capability.available
        return False

    def _require_capabilities(self, manifest: ExtensionManifest) -> None:
        required = tuple(manifest.capabilities.required)
        missing = [name for name in required if not self._capability_available(name)]
        if missing:
            raise ExtensionOperationError(
                "REQUIRED_CAPABILITY_UNAVAILABLE",
                "required extension capabilities are not available: " + ", ".join(sorted(missing)),
            )

    def _host_handler(self, record: ExtensionRecord) -> HostRequestHandler | None:
        manifest = record.manifest
        declared = set(manifest.capabilities.required) | set(manifest.capabilities.optional)
        routes: list[tuple[frozenset[str], HostRequestHandler]] = []
        data_access = self._data_access
        if (
            data_access is not None
            and data_access.available
            and CAPABILITY_EXTENSION_DATA in declared
        ):
            data_context = ExtensionDataContext(
                extension_id=manifest.id,
                extension_version=manifest.version,
                namespace=data_namespace(manifest.id),
                payload_root=Path(manifest.root),
            )

            async def data_handler(method: str, params: Mapping[str, Any]) -> Any:
                return await data_access.handle(method, params, context=data_context)

            routes.append((HOST_DATA_METHODS, data_handler))
        mail_capability = self._mail_capability
        if (
            mail_capability is not None
            and mail_capability.available
            and MAIL_READ_CAPABILITY in declared
        ):
            mail_context = MailCapabilityContext(
                extension_id=manifest.id, extension_version=manifest.version
            )

            async def mail_handler(method: str, params: Mapping[str, Any]) -> Any:
                return await mail_capability.handle(method, params, context=mail_context)

            routes.append((HOST_MAIL_METHODS, mail_handler))
        artifact_access = self._artifact_access
        if (
            artifact_access is not None
            and artifact_access.available
            and declared & {CAPABILITY_ARTIFACT_READ, CAPABILITY_ARTIFACT_WRITE}
        ):
            artifact_context = ExtensionArtifactContext(
                extension_id=manifest.id, extension_version=manifest.version
            )

            async def artifact_handler(method: str, params: Mapping[str, Any]) -> Any:
                return await artifact_access.handle(method, params, context=artifact_context)

            routes.append((HOST_ARTIFACT_METHODS, artifact_handler))
        browser_capability = self._browser_capability
        if (
            browser_capability is not None
            and browser_capability.available
            and BROWSER_READ_CAPABILITY in declared
        ):
            browser_context = BrowserCapabilityContext(
                extension_id=manifest.id, extension_version=manifest.version
            )

            async def browser_handler(method: str, params: Mapping[str, Any]) -> Any:
                return await browser_capability.handle(method, params, context=browser_context)

            routes.append((HOST_BROWSER_METHODS, browser_handler))
        if not routes:
            return None

        async def handler(method: str, params: Mapping[str, Any]) -> Any:
            for methods, route in routes:
                if method in methods:
                    return await route(method, params)
            raise ExtensionOperationError(
                "DATA_PROTOCOL_ERROR", f"host capability is not available: {method}"
            )

        return handler

    async def health(self, record: ExtensionRecord) -> bool:
        worker = self._workers.get(record.manifest.id)
        if worker is None or not worker.client.running:
            return False
        try:
            report = await worker.health(timeout_seconds=self._health_timeout)
        except (RpcCallError, RpcTimeoutError):
            return False
        return bool(report.get("healthy"))

    async def drain(
        self, record: ExtensionRecord, deadline_epoch: float
    ) -> Mapping[str, object] | None:
        """Bounded drain; the JSON-RPC client caps the whole wait at the deadline."""

        worker = self._workers.get(record.manifest.id)
        if worker is None or not worker.client.running:
            return None
        # A typed drain failure/timeout propagates so the operation records an
        # explicit outcome; the caller still stops the worker afterwards.
        report = await worker.client.drain(deadline_epoch, timeout_seconds=self._drain_timeout)
        return dict(report)

    async def stop(self, record: ExtensionRecord) -> None:
        extension_id = record.manifest.id
        self._records.pop(extension_id, None)
        worker = self._workers.pop(extension_id, None)
        if worker is not None:
            await worker.close()
        self._loaded.pop(extension_id, None)
        self._config_failures.pop(extension_id, None)
        store = self._config_store
        if isinstance(store, ExtensionConfigApplyStore):
            await store.report_runtime(
                extension_id,
                self._instance_id,
                loaded_digest=None,
                generation=None,
                state="STOPPED",
            )

    async def stop_all(self) -> None:
        if self._config_sync_task is not None:
            self._config_sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._config_sync_task
            self._config_sync_task = None
        self._records.clear()
        workers = list(self._workers.values())
        self._workers.clear()
        try:
            for worker in workers:
                await worker.close()
        finally:
            self._loaded.clear()
            self._config_failures.clear()
            if isinstance(self._config_store, ExtensionConfigApplyStore):
                await self._config_store.release_runtime(self._instance_id)

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
    ) -> Mapping[str, Any]:
        record = self._records.get(extension_id)
        external_write = record is not None and any(
            tool.id == tool_id and tool.risk == "EXTERNAL_WRITE" for tool in record.manifest.tools
        )
        async with self._configured_worker(
            extension_id,
            retain_inflight_result=external_write,
        ) as worker:
            deadline = datetime.now(UTC) + timedelta(seconds=deadline_seconds)
            context = {
                "task_id": task_id,
                "run_id": run_id,
                "deadline": deadline.isoformat(),
                "idempotency_key": idempotency_key,
                "context_handles": [],
                "artifact_handles": [],
                "capability_handles": [],
            }
            return await worker.invoke_tool(
                tool_id,
                dict(arguments),
                context,
                timeout_seconds=deadline_seconds,
            )

    async def list_forms(self, extension_id: str) -> list[Mapping[str, Any]]:
        async with self._configured_worker(extension_id) as worker:
            result = await worker.call_slot("form.list", {}, timeout_seconds=5.0)
            if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
                raise RpcCallError(-32094, "form.list result must be a list of objects")
            return result

    async def call_slot(self, extension_id: str, method: str, parameters: Mapping[str, Any]) -> Any:
        if method not in {
            "workflow.list",
            "schedule.list",
            "event_source.list",
            "event_source.poll",
            "context.retrieve",
        }:
            raise RpcCallError(-32601, "slot is not exposed by this host")
        async with self._configured_worker(extension_id) as worker:
            return await worker.call_slot(method, dict(parameters), timeout_seconds=30.0)

    def diagnostics(self, extension_id: str) -> tuple[str, ...]:
        worker = self._workers.get(extension_id)
        return worker.client.diagnostics if worker is not None else ()


class ProcessContractVerifier:
    """Starts a temporary worker and checks handshake, health and declared slots."""

    def __init__(
        self,
        *,
        handshake_timeout_seconds: float = 20.0,
        health_timeout_seconds: float = 10.0,
        call_timeout_seconds: float = 10.0,
        max_frame_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self._handshake_timeout = handshake_timeout_seconds
        self._health_timeout = health_timeout_seconds
        self._call_timeout = call_timeout_seconds
        self._max_frame_bytes = max_frame_bytes

    async def verify(self, installed: InstalledArtifact, manifest: ExtensionManifest) -> None:
        # Contract tests run against the installed payload, never the source tree.
        worker = _build_worker(
            manifest, Path(installed.install_path), max_frame_bytes=self._max_frame_bytes
        )
        try:
            await worker.start(timeout_seconds=self._handshake_timeout)
            report = await worker.health(timeout_seconds=self._health_timeout)
            if not report.get("healthy"):
                raise ExtensionOperationError(
                    "HEALTHCHECK_FAILED", "extension reported an unhealthy status"
                )
            await self._check_slots(worker, manifest)
        finally:
            # The temporary worker must be reaped even when verification is
            # cancelled or times out.
            await shield_cleanup(worker.close())

    async def _check_slots(self, worker: ExtensionWorker, manifest: ExtensionManifest) -> None:
        declared = manifest.slots
        if "ToolProvider" in declared:
            await self._check_tools(worker, manifest)
        for slot, method in _ID_SLOT_METHODS.items():
            if slot not in declared:
                continue
            expected_ids = set(getattr(manifest, _MANIFEST_SLOT_FIELDS[slot]))
            result = await worker.call_slot(method, {}, timeout_seconds=self._call_timeout)
            actual_ids = _returned_ids(result, method)
            if actual_ids != expected_ids:
                raise ExtensionOperationError(
                    "MANIFEST_INVALID",
                    f"{method} capabilities do not match the manifest",
                )
        if "ContextProvider" in declared:
            result = await worker.call_slot(
                "context.retrieve",
                {"query": {"text": "", "limit": 0}},
                timeout_seconds=self._call_timeout,
            )
            if not isinstance(result, list):
                raise ExtensionOperationError(
                    "MANIFEST_INVALID", "context.retrieve did not return a list"
                )
        if "MigrationProvider" in declared:
            result = await worker.call_slot(
                "migration.list", {}, timeout_seconds=self._call_timeout
            )
            if not isinstance(result, list) or len(result) != len(manifest.migrations):
                raise ExtensionOperationError(
                    "MANIFEST_INVALID", "migration.list does not match the manifest"
                )
        # NotificationProvider has no enumeration RPC and deliver() is a side
        # effect; the handshake slot comparison is the only pre-enable check.

    async def _check_tools(self, worker: ExtensionWorker, manifest: ExtensionManifest) -> None:
        result = await worker.call_slot("tool.list", {}, timeout_seconds=self._call_timeout)
        if not isinstance(result, list):
            raise ExtensionOperationError("MANIFEST_INVALID", "tool.list did not return a list")
        actual: dict[str, Mapping[str, Any]] = {}
        for item in result:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ExtensionOperationError(
                    "MANIFEST_INVALID", "tool.list returned an invalid descriptor"
                )
            actual[item["id"]] = item
        expected = {tool.id: tool for tool in manifest.tools}
        if set(actual) != set(expected):
            raise ExtensionOperationError(
                "MANIFEST_INVALID", "tool.list does not match the manifest tools"
            )
        for tool_id, tool in expected.items():
            descriptor = actual[tool_id]
            if descriptor.get("risk") != tool.risk:
                raise ExtensionOperationError(
                    "MANIFEST_INVALID",
                    f"tool {tool_id} risk does not match the manifest",
                )
            if descriptor.get("input_schema") != _load_schema(manifest, tool.input_schema):
                raise ExtensionOperationError(
                    "MANIFEST_INVALID",
                    f"tool {tool_id} input schema does not match the manifest",
                )
            if descriptor.get("output_schema") != _load_schema(manifest, tool.output_schema):
                raise ExtensionOperationError(
                    "MANIFEST_INVALID",
                    f"tool {tool_id} output schema does not match the manifest",
                )


_MANIFEST_SLOT_FIELDS = {
    "EventSource": "event_sources",
    "WorkflowProvider": "workflows",
    "ScheduleProvider": "schedules",
    "FormSchemaProvider": "forms",
}


def _returned_ids(result: object, method: str) -> set[str]:
    if not isinstance(result, list):
        raise ExtensionOperationError("MANIFEST_INVALID", f"{method} did not return a list")
    ids: set[str] = set()
    for item in result:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ExtensionOperationError(
                "MANIFEST_INVALID", f"{method} returned an invalid descriptor"
            )
        ids.add(item["id"])
    return ids


def _load_schema(manifest: ExtensionManifest, reference: str) -> object:
    return json.loads((manifest.root / reference).read_text("utf-8"))


__all__ = [
    "ProcessContractVerifier",
    "ProcessRuntimeSupervisor",
]
