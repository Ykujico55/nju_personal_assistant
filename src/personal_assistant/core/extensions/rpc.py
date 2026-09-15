"""Host-side stdio JSON-RPC client and extension worker facade."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import uuid
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from personal_assistant_sdk import PROTOCOL_VERSION
from personal_assistant_sdk.rpc import RpcRequest, decode_response, encode_frame

from .errors import RpcCallError, RpcTimeoutError
from .manifest import ExtensionManifest, compute_schema_hash


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    module: str
    python_executable: str = sys.executable
    cwd: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)


class JsonRpcProcessClient:
    """One-call-at-a-time client for a dedicated extension process.

    A timeout makes the stream correlation ambiguous, so the process is stopped
    and must be restarted by the supervisor.  Calls are never silently retried.
    """

    def __init__(
        self,
        spec: WorkerSpec,
        *,
        max_frame_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self._spec = spec
        self._max_frame_bytes = max_frame_bytes
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._stderr_task: asyncio.Task[None] | None = None
        self._diagnostics: deque[str] = deque(maxlen=100)
        self._broken = False

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None and not self._broken

    async def start(self) -> None:
        if self.running:
            return
        environment = os.environ.copy()
        environment.update(self._spec.environment)
        self._process = await asyncio.create_subprocess_exec(
            self._spec.python_executable,
            "-m",
            self._spec.module,
            cwd=self._spec.cwd,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=self._max_frame_bytes + 1,
        )
        self._broken = False
        self._stderr_task = asyncio.create_task(self._capture_stderr())

    async def call(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Any:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        async with self._lock:
            process = self._process
            if process is None or process.returncode is not None or self._broken:
                raise RpcCallError(-32090, "extension worker is not running")
            assert process.stdin is not None
            assert process.stdout is not None
            request = RpcRequest(id=f"call_{uuid.uuid4().hex}", method=method, params=params)
            encoded_request = encode_frame(request)
            if len(encoded_request) > self._max_frame_bytes:
                raise RpcCallError(-32600, "extension request exceeded maximum frame size")
            process.stdin.write(encoded_request)
            await process.stdin.drain()
            try:
                frame = await asyncio.wait_for(process.stdout.readline(), timeout_seconds)
            except TimeoutError as exc:
                self._broken = True
                await self._terminate()
                raise RpcTimeoutError(f"extension RPC timed out: {method}") from exc
            if not frame:
                self._broken = True
                code = process.returncode
                raise RpcCallError(-32091, f"extension worker exited unexpectedly ({code})")
            if len(frame) > self._max_frame_bytes:
                self._broken = True
                await self._terminate()
                raise RpcCallError(-32092, "extension response exceeded maximum frame size")
            response = decode_response(frame, max_bytes=self._max_frame_bytes)
            if response.id != request.id:
                self._broken = True
                await self._terminate()
                raise RpcCallError(-32093, "extension response id mismatch")
            if response.error is not None:
                raise RpcCallError(
                    response.error.code,
                    response.error.message,
                    dict(response.error.data),
                )
            return response.result

    async def close(self, *, graceful_timeout_seconds: float = 2.0) -> None:
        process = self._process
        if process is None:
            return
        if process.returncode is None and not self._broken:
            with contextlib.suppress(RpcCallError, RpcTimeoutError):
                await self.call(
                    "system.shutdown",
                    _base_params("", ""),
                    timeout_seconds=graceful_timeout_seconds,
                )
        await self._terminate()

    async def _terminate(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self._stderr_task is not None:
            if not self._stderr_task.done():
                self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task
        self._process = None

    async def _capture_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while line := await process.stderr.readline():
            # Local diagnostic only; never include these lines in user-facing RPC errors.
            self._diagnostics.append(line.decode("utf-8", errors="replace").rstrip())


class ExtensionWorker:
    """Identity-checking facade over the generic process client."""

    def __init__(self, manifest: ExtensionManifest, client: JsonRpcProcessClient) -> None:
        self.manifest = manifest
        self.client = client
        self._handshaken = False

    async def start(
        self,
        *,
        non_secret_config: Mapping[str, Any] | None = None,
        capability_handles: Sequence[Mapping[str, Any]] = (),
        timeout_seconds: float = 10.0,
    ) -> Mapping[str, Any]:
        await self.client.start()
        expected_schema_hash = compute_schema_hash(self.manifest)
        result = await self.client.call(
            "system.handshake",
            {
                **_base_params(self.manifest.id, self.manifest.version),
                "runtime": {
                    "protocol_version": PROTOCOL_VERSION,
                    "extension_id": self.manifest.id,
                    "extension_version": self.manifest.version,
                    "data_namespace": _data_namespace(self.manifest.id),
                    "manifest_schema_hash": expected_schema_hash,
                    "non_secret_config": dict(non_secret_config or {}),
                    "capability_handles": list(capability_handles),
                },
            },
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(result, dict):
            raise RpcCallError(-32094, "handshake result must be an object")
        expected = {
            "id": self.manifest.id,
            "version": self.manifest.version,
            "protocol_version": PROTOCOL_VERSION,
            "schema_hash": expected_schema_hash,
        }
        mismatches = {
            key: {"expected": value, "actual": result.get(key)}
            for key, value in expected.items()
            if result.get(key) != value
        }
        declared_slots = set(self.manifest.slots)
        actual_slots = (
            set(result.get("slots", []))
            if isinstance(result.get("slots"), list)
            else set()
        )
        if actual_slots != declared_slots:
            mismatches["slots"] = {
                "expected": sorted(declared_slots),
                "actual": sorted(actual_slots),
            }
        if mismatches:
            await self.client.close()
            raise RpcCallError(-32095, "worker handshake does not match manifest", mismatches)
        self._handshaken = True
        return result

    async def health(self, *, timeout_seconds: float = 5.0) -> Mapping[str, Any]:
        result = await self._call("system.health", {}, timeout_seconds)
        if not isinstance(result, dict):
            raise RpcCallError(-32094, "health result must be an object")
        return result

    async def invoke_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        if tool_id not in {tool.id for tool in self.manifest.tools}:
            raise RpcCallError(-32601, f"tool not declared by manifest: {tool_id}")
        result = await self._call(
            "tool.invoke",
            {"tool_id": tool_id, "arguments": dict(arguments), "context": dict(context)},
            timeout_seconds,
        )
        if not isinstance(result, dict):
            raise RpcCallError(-32094, "tool result must be an object")
        return result

    async def call_slot(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float = 5.0,
    ) -> Any:
        return await self._call(method, params, timeout_seconds)

    async def drain(self, deadline_epoch: float, *, timeout_seconds: float = 5.0) -> Any:
        return await self._call(
            "system.drain", {"deadline": deadline_epoch}, timeout_seconds
        )

    async def close(self) -> None:
        await self.client.close()
        self._handshaken = False

    async def _call(self, method: str, params: Mapping[str, Any], timeout: float) -> Any:
        if not self._handshaken:
            raise RpcCallError(-32001, "extension worker has not completed handshake")
        return await self.client.call(
            method,
            {**_base_params(self.manifest.id, self.manifest.version), **dict(params)},
            timeout_seconds=timeout,
        )


def _base_params(extension_id: str, extension_version: str) -> dict[str, str]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "extension_id": extension_id,
        "extension_version": extension_version,
    }


def _data_namespace(extension_id: str) -> str:
    safe = "".join(character if character.isalnum() else "_" for character in extension_id)
    return f"ext_{safe}"
