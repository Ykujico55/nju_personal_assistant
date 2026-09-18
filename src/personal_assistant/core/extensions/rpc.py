"""Host-side stdio JSON-RPC client and extension worker facade.

The channel is full duplex.  The host drives the worker with stable JSON-RPC
requests; the worker may send its own requests back for generic host
capabilities (for example the extension data broker).  Every correlation
failure, timeout or malformed frame stops the process and surfaces a typed
error; nothing is silently retried.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from personal_assistant_sdk import PROTOCOL_VERSION
from personal_assistant_sdk.rpc import (
    RpcError,
    RpcProtocolError,
    RpcRequest,
    RpcResponse,
    decode_frame,
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

# A worker-initiated request handler: ``handler(method, params) -> result``.
HostRequestHandler = Callable[[str, Mapping[str, Any]], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    module: str
    python_executable: str = sys.executable
    cwd: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    host_handler: HostRequestHandler | None = field(default=None, compare=False)


class JsonRpcProcessClient:
    """Full-duplex client for a dedicated extension process.

    Host calls are serialized one at a time.  Worker-initiated host-capability
    requests are handled concurrently by the reader so a call in flight can
    always reach the host.  A timeout makes the stream correlation ambiguous, so
    the process is stopped and must be restarted by the supervisor.  Calls are
    never silently retried.
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
        self._reader_task: asyncio.Task[None] | None = None
        self._diagnostics: deque[str] = deque(maxlen=100)
        self._broken = False
        self._in_flight = False
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._failure: RpcCallError | None = None

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
        self._failure = None
        self._pending = {}
        self._stderr_task = asyncio.create_task(self._capture_stderr())
        self._reader_task = asyncio.create_task(self._read_loop())

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
        """Send one request and await its correlated response; caller holds the lock."""

        process = self._process
        if process is None or process.returncode is not None or self._broken:
            raise RpcCallError(-32090, "extension worker is not running")
        assert process.stdin is not None
        request = RpcRequest(id=f"call_{uuid.uuid4().hex}", method=method, params=params)
        encoded_request = encode_frame(request)
        if len(encoded_request) > self._max_frame_bytes:
            raise RpcCallError(-32600, "extension request exceeded maximum frame size")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request.id] = future
        try:
            process.stdin.write(encoded_request)
            await process.stdin.drain()
        except asyncio.CancelledError:
            # The correlation with the in-flight request is lost; the worker can
            # never be trusted again, so it is stopped before re-raising.
            self._pending.pop(request.id, None)
            await shield_cleanup(self._break())
            raise
        except (OSError, ValueError) as exc:
            self._pending.pop(request.id, None)
            await self._break()
            raise RpcCallError(
                -32091, "extension worker closed its input stream"
            ) from exc
        try:
            return await asyncio.wait_for(future, timeout_seconds)
        except TimeoutError as exc:
            await self._break()
            raise RpcTimeoutError(f"extension RPC timed out: {method}") from exc
        except asyncio.CancelledError:
            # The correlation with the in-flight request is lost on cancellation.
            await shield_cleanup(self._break())
            raise
        finally:
            self._pending.pop(request.id, None)

    async def _read_loop(self) -> None:
        """Route frames: worker requests are handled, responses are correlated."""

        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                try:
                    frame = await process.stdout.readline()
                except ValueError:
                    await self._fail(
                        RpcCallError(
                            -32092, "extension response exceeded maximum frame size"
                        )
                    )
                    return
                if not frame:
                    code = process.returncode
                    await self._fail(
                        RpcCallError(
                            -32091, f"extension worker exited unexpectedly ({code})"
                        )
                    )
                    return
                if len(frame) > self._max_frame_bytes:
                    await self._fail(
                        RpcCallError(
                            -32092, "extension response exceeded maximum frame size"
                        )
                    )
                    return
                try:
                    message = decode_frame(frame, max_bytes=self._max_frame_bytes)
                except RpcProtocolError:
                    await self._fail(
                        RpcCallError(-32700, "extension sent an invalid JSON-RPC frame")
                    )
                    return
                if isinstance(message, RpcRequest):
                    await self._handle_host_request(message)
                    continue
                future = self._pending.pop(message.id or "", None)
                if message.id is None or future is None:
                    await self._fail(
                        RpcCallError(-32093, "extension response id mismatch")
                    )
                    return
                if future.done():
                    continue
                if message.error is not None:
                    future.set_exception(
                        RpcCallError(
                            message.error.code,
                            message.error.message,
                            dict(message.error.data),
                        )
                    )
                else:
                    future.set_result(message.result)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._fail(RpcCallError(-32100, "extension stream failed unexpectedly"))

    async def _handle_host_request(self, request: RpcRequest) -> None:
        handler = self._spec.host_handler
        if handler is None:
            response = RpcResponse(
                id=request.id,
                error=RpcError(
                    -32601,
                    f"host capability is not available: {request.method}",
                    {"code": "DATA_UNAVAILABLE", "retryable": False},
                ),
            )
        else:
            try:
                result = await handler(request.method, request.params)
                response = RpcResponse(id=request.id, result=_json_result(result))
            except ExtensionOperationError as exc:
                response = RpcResponse(
                    id=request.id,
                    error=RpcError(
                        -32100,
                        str(exc),
                        {"code": _safe_host_code(exc.code), "retryable": False},
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                response = RpcResponse(
                    id=request.id,
                    error=RpcError(
                        -32100,
                        "host capability failed",
                        {"code": "DATA_INTERNAL_ERROR", "retryable": False},
                    ),
                )
        encoded = encode_frame(response)
        if len(encoded) > self._max_frame_bytes:
            encoded = encode_frame(
                RpcResponse(
                    id=request.id,
                    error=RpcError(
                        -32101,
                        "host capability result exceeded the frame limit",
                        {"code": "DATA_RESULT_TOO_LARGE", "retryable": False},
                    ),
                )
            )
        try:
            process = self._process
            if process is None or process.stdin is None:
                return
            process.stdin.write(encoded)
            await process.stdin.drain()
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError):
            await self._fail(
                RpcCallError(-32091, "extension worker closed its input stream")
            )

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

    async def _fail(self, error: RpcCallError) -> None:
        """Mark the stream failed, fail every waiter, and stop the process."""

        self._broken = True
        self._failure = error
        self._fail_pending(error)
        await self._terminate(bounded=True)

    async def _break(self) -> None:
        """Correlate the broken stream with a stopped process, never a retry."""

        self._broken = True
        error = self._failure or RpcCallError(
            -32090, "extension worker is not running"
        )
        self._fail_pending(error)
        await self._terminate(bounded=True)

    def _fail_pending(self, error: RpcCallError) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    async def _terminate(
        self,
        *,
        grace_seconds: float = 2.0,
        bounded: bool = False,
    ) -> None:
        # Swap state out first so a concurrent reader/waiter failure is a no-op
        # instead of a second full teardown racing the first one.
        process = self._process
        reader = self._reader_task
        stderr = self._stderr_task
        self._process = None
        self._reader_task = None
        self._stderr_task = None
        current = asyncio.current_task()
        if process is not None:
            if bounded:
                # A broken/timed-out stream only needs the process stopped; the
                # OS exit notification must never stretch a caller's deadline.
                await self._stop_bounded(process, timeout_seconds=0.25)
            else:
                await terminate_process(process, grace_seconds=grace_seconds)
        if reader is not None and reader is not current and not reader.done():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(reader, 1.0)
        if stderr is not None and stderr is not current and not stderr.done():
            stderr.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(stderr, 1.0)

    @staticmethod
    async def _stop_bounded(
        process: asyncio.subprocess.Process, *, timeout_seconds: float
    ) -> None:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout_seconds)
        except TimeoutError:
            # Reap in the background; the kill signal was already delivered, so
            # the process is stopping even if the OS exit event is slow.
            asyncio.get_running_loop().create_task(_reap(process))

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


async def _reap(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(Exception):
        await process.wait()


def _json_result(result: Any) -> Any:
    """Host capability results must be strict JSON values."""

    if result is None:
        return {}
    if isinstance(result, (bool, int, float, str, Mapping, list, tuple)):
        return result
    raise TypeError(f"host capability returned unsupported value type: {type(result).__name__}")


def _safe_host_code(code: object) -> object:
    if isinstance(code, str) and code and len(code) <= 64:
        return code
    return "DATA_INTERNAL_ERROR"


def _base_params(extension_id: str, extension_version: str) -> dict[str, str]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "extension_id": extension_id,
        "extension_version": extension_version,
    }
