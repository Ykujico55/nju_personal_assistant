"""A small stdio worker runtime for trusted Python extensions.

The worker boundary isolates dependencies and crashes; it is deliberately not a
security sandbox.  A user who installs an extension trusts code running under the
same operating-system account.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from . import PROTOCOL_VERSION
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
from .rpc import RpcError, RpcProtocolError, RpcRequest, RpcResponse, decode_request, encode_frame


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

    while True:
        frame = await reader.readline()
        if not frame:
            break
        request_id: str | None = None
        try:
            request = decode_request(frame, max_bytes=max_frame_bytes)
            request_id = request.id
            response = await dispatcher.dispatch(request)
        except RpcProtocolError as exc:
            response = RpcResponse(
                id=request_id,
                error=RpcError(-32700, str(exc), {"outcome": "PERMANENT"}),
            )
        writer.write(encode_frame(response))
        await writer.drain()
        if request_id is not None and request.method == "system.shutdown":
            break


def run_stdio_worker(extension_factory: Callable[[], Extension]) -> None:
    """Run a worker over process stdin/stdout.

    ``asyncio`` pipe hookup is not portable for every Windows event loop, so the
    reference runner delegates blocking stdio reads/writes to worker threads.
    """

    asyncio.run(_serve_blocking_stdio(ExtensionDispatcher(extension_factory)))


async def _serve_blocking_stdio(dispatcher: ExtensionDispatcher) -> None:
    while True:
        frame = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not frame:
            return
        request_id: str | None = None
        method: str | None = None
        try:
            request = decode_request(frame)
            request_id = request.id
            method = request.method
            response = await dispatcher.dispatch(request)
        except RpcProtocolError as exc:
            response = RpcResponse(
                id=request_id,
                error=RpcError(-32700, str(exc), {"outcome": "PERMANENT"}),
            )
        encoded = encode_frame(response)
        await asyncio.to_thread(_write_stdout, encoded)
        if method == "system.shutdown":
            return


def _write_stdout(frame: bytes) -> None:
    sys.stdout.buffer.write(frame)
    sys.stdout.buffer.flush()


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value
