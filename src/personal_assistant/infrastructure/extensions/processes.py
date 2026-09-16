"""Real worker-process supervision over newline-delimited JSON-RPC 2.0.

Each extension version runs in its own subprocess started from that version's
venv.  A broken stream, timeout, oversized frame or unexpected exit stops the
process and surfaces a typed error; nothing is silently retried here.  The venv
and process isolate dependencies and crashes, not malicious code.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from personal_assistant.core.extensions.async_utils import shield_cleanup
from personal_assistant.core.extensions.errors import (
    ExtensionOperationError,
    RpcCallError,
    RpcTimeoutError,
)
from personal_assistant.core.extensions.lifecycle import InstalledArtifact
from personal_assistant.core.extensions.manifest import ExtensionManifest
from personal_assistant.core.extensions.models import ExtensionRecord
from personal_assistant.core.extensions.rpc import (
    ExtensionWorker,
    JsonRpcProcessClient,
    WorkerSpec,
)

from .installer import venv_python

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
        ),
        max_frame_bytes=max_frame_bytes,
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
    ) -> None:
        self._handshake_timeout = handshake_timeout_seconds
        self._health_timeout = health_timeout_seconds
        self._drain_timeout = drain_timeout_seconds
        self._max_frame_bytes = max_frame_bytes
        self._workers: dict[str, ExtensionWorker] = {}
        self._start_locks: dict[str, asyncio.Lock] = {}

    async def start(self, record: ExtensionRecord) -> None:
        extension_id = record.manifest.id
        # Serialize starts per extension so a racing enable can never spawn two
        # workers and leak the one the supervisor does not track.
        lock = self._start_locks.setdefault(extension_id, asyncio.Lock())
        async with lock:
            existing = self._workers.get(extension_id)
            if existing is not None and existing.client.running:
                return
            if existing is not None:
                await existing.close()
                self._workers.pop(extension_id, None)
            version_dir = Path(record.install_path or "")
            worker = _build_worker(
                record.manifest, version_dir, max_frame_bytes=self._max_frame_bytes
            )
            try:
                await worker.start(timeout_seconds=self._handshake_timeout)
            except BaseException:
                await worker.close()
                raise
            self._workers[extension_id] = worker

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
        report = await worker.client.drain(
            deadline_epoch, timeout_seconds=self._drain_timeout
        )
        return dict(report)

    async def stop(self, record: ExtensionRecord) -> None:
        worker = self._workers.pop(record.manifest.id, None)
        if worker is not None:
            await worker.close()

    async def stop_all(self) -> None:
        workers = list(self._workers.items())
        self._workers.clear()
        for _, worker in workers:
            await worker.close()

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
        worker = self._workers.get(extension_id)
        if worker is None or not worker.client.running:
            raise RpcCallError(-32090, "extension worker is not running")
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

    async def verify(
        self, installed: InstalledArtifact, manifest: ExtensionManifest
    ) -> None:
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

    async def _check_tools(
        self, worker: ExtensionWorker, manifest: ExtensionManifest
    ) -> None:
        result = await worker.call_slot("tool.list", {}, timeout_seconds=self._call_timeout)
        if not isinstance(result, list):
            raise ExtensionOperationError(
                "MANIFEST_INVALID", "tool.list did not return a list"
            )
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
