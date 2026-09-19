"""A small stdio worker runtime for trusted Python extensions.

The worker boundary isolates dependencies and crashes; it is deliberately not a
security sandbox.  A user who installs an extension trusts code running under the
same operating-system account.

The stream is full duplex: the host drives the worker through stable JSON-RPC
methods, and the worker may send its own requests back to the host (for example
the generic extension data capability) over the same channel.  Frames carrying
``method`` are requests; all other frames are responses.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

from . import PROTOCOL_VERSION
from .host import HostBroker
from .models import (
    ContextQuery,
    InvocationContext,
    NotificationRequest,
    PollRequest,
    RuntimeContext,
    from_mapping,
    to_jsonable,
)
from .protocols import (
    ContextProvider,
    EventSource,
    Extension,
    FormSchemaProvider,
    MigrationProvider,
    NotificationProvider,
    ScheduleProvider,
    ToolProvider,
    WorkflowProvider,
)
from .rpc import (
    RpcError,
    RpcProtocolError,
    RpcRequest,
    RpcResponse,
    decode_frame,
    encode_frame,
)


class WorkerDispatchError(Exception):
    def __init__(self, code: int, message: str, outcome: str = "PERMANENT") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.outcome = outcome


class ExtensionDispatcher:
    """Maps the stable RPC surface to structural SDK protocols."""

    def __init__(
        self,
        extension_factory: Callable[[], Extension],
        *,
        max_result_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self._factory = extension_factory
        self._extension: Extension | None = None
        self._initialized = False
        self._draining = False
        self._active_calls = 0
        self._max_result_bytes = max_result_bytes
        self._host_broker: HostBroker | None = None

    def attach_host_broker(self, broker: HostBroker) -> None:
        self._host_broker = broker

    async def dispatch(self, request: RpcRequest) -> RpcResponse:
        try:
            result = await self._invoke(request.method, request.params)
            encoded = encode_frame(RpcResponse(id=request.id, result=to_jsonable(result)))
            if len(encoded) > self._max_result_bytes:
                raise WorkerDispatchError(-32002, "result exceeds configured maximum")
            return RpcResponse(id=request.id, result=to_jsonable(result))
        except WorkerDispatchError as exc:
            return RpcResponse(
                id=request.id,
                error=RpcError(exc.code, exc.message, {"outcome": exc.outcome}),
            )
        except (TypeError, ValueError, KeyError) as exc:
            return RpcResponse(
                id=request.id,
                error=RpcError(-32602, str(exc), {"outcome": "PERMANENT"}),
            )
        except Exception:
            # Never expose a raw traceback over RPC.  Host-side diagnostics use the
            # opaque id; deployments may attach a local structured-log sink here.
            diagnostic_id = f"diag_{uuid.uuid4().hex}"
            return RpcResponse(
                id=request.id,
                error=RpcError(
                    -32000,
                    "extension call failed",
                    {"outcome": "UNKNOWN", "diagnostic_id": diagnostic_id},
                ),
            )

    async def _invoke(self, method: str, params: Mapping[str, Any]) -> Any:
        if method == "system.handshake":
            return await self._handshake(params)
        extension = self._require_initialized()
        if method == "system.health":
            return await extension.health()
        if method == "system.drain":
            self._draining = True
            return await extension.drain(float(params["deadline"]))
        if method == "system.shutdown":
            self._draining = True
            await extension.shutdown()
            return {"shutdown": True}
        if self._draining:
            raise WorkerDispatchError(-32003, "extension is draining", "RETRYABLE")

        self._active_calls += 1
        try:
            if method == "tool.list":
                return self._provider(extension, ToolProvider).tools()
            if method == "tool.invoke":
                provider = self._provider(extension, ToolProvider)
                return await provider.invoke(
                    str(params["tool_id"]),
                    _object(params.get("arguments", {}), "arguments"),
                    from_mapping(
                        InvocationContext,
                        _object(params["context"], "context"),
                    ),
                )
            if method == "context.retrieve":
                provider = self._provider(extension, ContextProvider)
                return await provider.retrieve(
                    from_mapping(ContextQuery, _object(params["query"], "query"))
                )
            if method == "event_source.list":
                return self._provider(extension, EventSource).event_sources()
            if method == "event_source.poll":
                provider = self._provider(extension, EventSource)
                return await provider.poll(
                    from_mapping(PollRequest, _object(params["request"], "request"))
                )
            if method == "workflow.list":
                return self._provider(extension, WorkflowProvider).workflows()
            if method == "schedule.list":
                return self._provider(extension, ScheduleProvider).schedules()
            if method == "notification.deliver":
                provider = self._provider(extension, NotificationProvider)
                return await provider.deliver(
                    from_mapping(
                        NotificationRequest,
                        _object(params["request"], "request"),
                    )
                )
            if method == "migration.list":
                return self._provider(extension, MigrationProvider).migrations()
            if method == "form.list":
                return self._provider(extension, FormSchemaProvider).forms()
            raise WorkerDispatchError(-32601, f"method not found: {method}")
        finally:
            self._active_calls -= 1

    async def _handshake(self, params: Mapping[str, Any]) -> Any:
        if self._initialized:
            raise WorkerDispatchError(-32004, "worker is already initialized")
        protocol_version = str(params.get("protocol_version", ""))
        if protocol_version != PROTOCOL_VERSION:
            raise WorkerDispatchError(-32005, "incompatible protocol version")
        runtime = from_mapping(
            RuntimeContext,
            _object(params.get("runtime", {}), "runtime"),
        )
        if runtime.protocol_version != protocol_version:
            raise WorkerDispatchError(-32005, "runtime protocol version mismatch")
        if self._host_broker is not None:
            runtime = replace(
                runtime,
                host_data=self._host_broker,
                host_mail=self._host_broker.mail,
                host_artifact=self._host_broker.artifact,
            )
        extension = self._factory()
        if not isinstance(extension, Extension):
            raise WorkerDispatchError(-32006, "entrypoint does not implement Extension")
        info = await extension.initialize(runtime)
        if info.id != runtime.extension_id or info.version != runtime.extension_version:
            await extension.shutdown()
            raise WorkerDispatchError(-32007, "extension identity does not match runtime")
        if info.protocol_version != protocol_version:
            await extension.shutdown()
            raise WorkerDispatchError(-32005, "extension protocol version mismatch")
        self._extension = extension
        self._initialized = True
        return info

    def _require_initialized(self) -> Extension:
        if not self._initialized or self._extension is None:
            raise WorkerDispatchError(-32001, "worker has not completed handshake")
        return self._extension

    @staticmethod
    def _provider(extension: Extension, protocol: type[Any]) -> Any:
        if not isinstance(extension, protocol):
            raise WorkerDispatchError(-32601, f"slot not implemented: {protocol.__name__}")
        return extension


async def serve_streams(
    dispatcher: ExtensionDispatcher,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    max_frame_bytes: int = 1024 * 1024,
) -> None:
    """Serve newline-delimited JSON-RPC until EOF or shutdown."""

    async def next_frame() -> bytes | None:
        frame = await reader.readline()
        return frame or None

    async def write_frame(frame: bytes) -> None:
        writer.write(frame)
        await writer.drain()

    await _serve_full_duplex(
        dispatcher, next_frame, write_frame, max_frame_bytes=max_frame_bytes
    )


async def _serve_full_duplex(
    dispatcher: ExtensionDispatcher,
    next_frame: Callable[[], Awaitable[bytes | None]],
    write_frame: Callable[[bytes], Awaitable[None]],
    *,
    max_frame_bytes: int,
) -> None:
    write_lock = asyncio.Lock()

    async def send(encoded: bytes) -> None:
        async with write_lock:
            await write_frame(encoded)

    broker = HostBroker(send, max_frame_bytes=max_frame_bytes)
    dispatcher.attach_host_broker(broker)
    requests: asyncio.Queue[RpcRequest | None] = asyncio.Queue()
    fatal = asyncio.Event()

    async def reader_loop() -> None:
        try:
            while True:
                frame = await next_frame()
                if frame is None:
                    requests.put_nowait(None)
                    return
                try:
                    message = decode_frame(frame, max_bytes=max_frame_bytes)
                except RpcProtocolError:
                    await send(
                        encode_frame(
                            RpcResponse(
                                id=None,
                                error=RpcError(
                                    -32700, "invalid JSON-RPC frame", {"outcome": "PERMANENT"}
                                ),
                            )
                        )
                    )
                    continue
                if isinstance(message, RpcRequest):
                    requests.put_nowait(message)
                    continue
                if not broker.resolve(message):
                    # A response that matches no pending call destroys stream
                    # correlation; the worker stops instead of guessing.
                    fatal.set()
                    requests.put_nowait(None)
                    return
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError):
            fatal.set()
            requests.put_nowait(None)

    reader_task = asyncio.create_task(reader_loop())
    try:
        while True:
            request = await requests.get()
            if request is None:
                return
            response = await dispatcher.dispatch(request)
            await send(encode_frame(response))
            if request.method == "system.shutdown":
                return
    finally:
        broker.close()
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader_task


def run_stdio_worker(extension_factory: Callable[[], Extension]) -> None:
    """Run a worker over process stdin/stdout.

    ``asyncio`` pipe hookup is not portable for every Windows event loop, so a
    dedicated daemon thread feeds stdin frames into the loop.  Daemon threads do
    not block interpreter exit after ``system.shutdown``.
    """

    asyncio.run(_serve_blocking_stdio(ExtensionDispatcher(extension_factory)))


async def _serve_blocking_stdio(dispatcher: ExtensionDispatcher) -> None:
    loop = asyncio.get_running_loop()
    frames: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _read_stdin() -> None:
        while True:
            try:
                frame = sys.stdin.buffer.readline()
            except (OSError, ValueError):
                frame = b""
            loop.call_soon_threadsafe(frames.put_nowait, frame or None)
            if not frame:
                return

    reader_thread = threading.Thread(
        target=_read_stdin, name="extension-stdin", daemon=True
    )
    reader_thread.start()

    async def next_frame() -> bytes | None:
        return await frames.get()

    async def write_frame(frame: bytes) -> None:
        sys.stdout.buffer.write(frame)
        sys.stdout.buffer.flush()

    await _serve_full_duplex(
        dispatcher, next_frame, write_frame, max_frame_bytes=1024 * 1024
    )


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


__all__ = [
    "ExtensionDispatcher",
    "WorkerDispatchError",
    "run_stdio_worker",
    "serve_streams",
]
