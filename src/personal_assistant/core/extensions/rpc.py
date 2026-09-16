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
from datetime import UTC, datetime
from typing import Any

from personal_assistant_sdk import PROTOCOL_VERSION
from personal_assistant_sdk.rpc import (
    RpcProtocolError,
    RpcRequest,
    decode_response,
    encode_frame,
)

from .async_utils import shield_cleanup, terminate_process
from .errors import ExtensionOperationError, RpcCallError, RpcTimeoutError
from .manifest import ExtensionManifest, compute_schema_hash
from .models import data_namespace

# Worker processes receive a minimal environment only.  Credentials, tokens,
# cookies and database URLs must never be inherited from the host process;
# callers may add explicitly declared non-sensitive values through WorkerSpec.
_SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
    }
)


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
        self._in_flight = False

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None and not self._broken

    @property
    def in_flight(self) -> bool:
        return self._in_flight

    async def start(self) -> None:
        if self.running:
            return
        environment = {
            key: os.environ[key] for key in _SAFE_ENVIRONMENT_KEYS if key in os.environ
        }
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
        loop = asyncio.get_running_loop()
        # A single monotonic deadline covers waiting for the stream lock, the
        # write and the response; a queued call may not exceed its budget.
        deadline = loop.time() + timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                await self._lock.acquire()
                try:
                    self._in_flight = True
                    budget = deadline - loop.time()
                    if budget <= 0:
                        raise TimeoutError
                    return await self._exchange(method, params, budget)
                finally:
                    self._in_flight = False
                    self._lock.release()
        except TimeoutError as exc:
            await self._break()
            raise RpcTimeoutError(f"extension RPC timed out: {method}") from exc
        except asyncio.CancelledError:
            await shield_cleanup(self._break())
            raise

    async def drain(
        self,
        deadline_epoch: float,
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        """Bounded drain: the whole wait, including an in-flight call, is capped.

        The wall-clock deadline is converted once to a monotonic deadline that
        covers waiting for the lock, writing and reading.  When it expires the
        worker is stopped and ``RpcTimeoutError`` is raised; there is no silent
        retry.  The returned report must be clean (``drained is True`` and
        ``active_calls == 0``); anything else fails closed.
        """

        loop = asyncio.get_running_loop()
        now = datetime.now(UTC).timestamp()
        remaining = min(timeout_seconds, max(0.0, deadline_epoch - now))
        if remaining <= 0:
            await self._break()
            raise RpcTimeoutError("extension drain deadline already expired")
        deadline = loop.time() + remaining
        try:
            async with asyncio.timeout_at(deadline):
                await self._lock.acquire()
                try:
                    self._in_flight = True
                    budget = deadline - loop.time()
                    if budget <= 0:
                        raise TimeoutError
                    result = await self._exchange(
                        "system.drain", {"deadline": deadline_epoch}, budget
                    )
                finally:
                    self._in_flight = False
                    self._lock.release()
        except TimeoutError as exc:
            await self._break()
            raise RpcTimeoutError(
                "extension drain deadline exceeded with a call in flight"
            ) from exc
        except asyncio.CancelledError:
            await shield_cleanup(self._break())
            raise
        if not isinstance(result, Mapping):
            raise RpcCallError(-32094, "drain result must be an object")
        drained = result.get("drained")
        active_calls = result.get("active_calls")
        if drained is not True or not isinstance(active_calls, int) or active_calls != 0:
            raise ExtensionOperationError(
                "DRAIN_TIMEOUT",
                "extension drain report is not clean (drained must be true and "
                "active_calls must be zero)",
            )
        return result

    async def _exchange(
        self, method: str, params: Mapping[str, Any], timeout_seconds: float
    ) -> Any:
        """Send one request and read one response; caller holds ``self._lock``."""

        process = self._process
        if process is None or process.returncode is not None or self._broken:
            raise RpcCallError(-32090, "extension worker is not running")
        assert process.stdin is not None
        assert process.stdout is not None
        request = RpcRequest(id=f"call_{uuid.uuid4().hex}", method=method, params=params)
        encoded_request = encode_frame(request)
        if len(encoded_request) > self._max_frame_bytes:
            raise RpcCallError(-32600, "extension request exceeded maximum frame size")
        try:
            process.stdin.write(encoded_request)
            await process.stdin.drain()
        except asyncio.CancelledError:
            # The correlation with the in-flight request is lost; the worker can
            # never be trusted again, so it is stopped before re-raising.
            await shield_cleanup(self._break())
            raise
        except (OSError, ValueError) as exc:
            await self._break()
            raise RpcCallError(
                -32091, "extension worker closed its input stream"
            ) from exc
        try:
            frame = await asyncio.wait_for(process.stdout.readline(), timeout_seconds)
        except TimeoutError as exc:
            await self._break()
            raise RpcTimeoutError(f"extension RPC timed out: {method}") from exc
        except asyncio.CancelledError:
            # The correlation with the in-flight request is lost on cancellation.
            await shield_cleanup(self._break())
            raise
        except ValueError as exc:
            # The asyncio stream limit was exceeded before a newline arrived.
            await self._break()
            raise RpcCallError(
                -32092, "extension response exceeded maximum frame size"
            ) from exc
        if not frame:
            await self._break()
            code = process.returncode
            raise RpcCallError(-32091, f"extension worker exited unexpectedly ({code})")
        if len(frame) > self._max_frame_bytes:
            await self._break()
            raise RpcCallError(-32092, "extension response exceeded maximum frame size")
        try:
            response = decode_response(frame, max_bytes=self._max_frame_bytes)
        except RpcProtocolError as exc:
            await self._break()
            raise RpcCallError(
                -32700, "extension sent an invalid JSON-RPC frame"
            ) from exc
        if response.id != request.id:
            await self._break()
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

    async def _break(self) -> None:
        """Correlate the broken stream with a stopped process, never a retry."""

        self._broken = True
        await self._terminate()

    async def _terminate(self) -> None:
        process = self._process
        if process is not None:
            await terminate_process(process)
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
                    "data_namespace": data_namespace(self.manifest.id),
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
