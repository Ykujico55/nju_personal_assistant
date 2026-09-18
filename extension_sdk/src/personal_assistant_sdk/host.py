"""Worker-side client for generic host capabilities.

The worker and the host share one newline-delimited JSON-RPC stream.  The host
sends requests (handshake, tools, context, events); the worker uses this broker
to send its own requests back for host-owned resources such as the generic
extension data capability.  No database credentials, connection strings or
absolute host paths ever cross this boundary.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .models import JsonValue
from .rpc import RpcError, RpcRequest, RpcResponse, encode_frame

HOST_DATA_EXECUTE = "host.data.execute"
HOST_DATA_TRANSACTION = "host.data.transaction"
HOST_DATA_MIGRATE = "host.data.migrate"

DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 900.0


class HostCapabilityError(RuntimeError):
    """A typed failure returned by the host capability broker."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class HostBroker:
    """Sends host-capability requests and awaits correlated responses."""

    def __init__(
        self,
        write_frame: Callable[[bytes], Awaitable[None]],
        *,
        max_frame_bytes: int = 1_048_576,
        default_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._write_frame = write_frame
        self._max_frame_bytes = max_frame_bytes
        self._default_timeout = default_timeout_seconds
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._closed = False

    @property
    def pending(self) -> int:
        return len(self._pending)

    async def execute(
        self,
        statement: str,
        parameters: Sequence[JsonValue] = (),
        *,
        timeout_seconds: float = 30.0,
    ) -> Mapping[str, JsonValue]:
        result = await self.request(
            HOST_DATA_EXECUTE,
            {
                "statement": statement,
                "parameters": list(parameters),
                "timeout_seconds": timeout_seconds,
            },
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(result, Mapping):
            raise HostCapabilityError("DATA_PROTOCOL_ERROR", "host returned a non-object result")
        return result

    async def transaction(
        self,
        statements: Sequence[Mapping[str, JsonValue]],
        *,
        timeout_seconds: float = 60.0,
    ) -> Mapping[str, JsonValue]:
        result = await self.request(
            HOST_DATA_TRANSACTION,
            {
                "statements": [dict(item) for item in statements],
                "timeout_seconds": timeout_seconds,
            },
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(result, Mapping):
            raise HostCapabilityError("DATA_PROTOCOL_ERROR", "host returned a non-object result")
        return result

    async def migrate(
        self,
        migrations: Sequence[Mapping[str, JsonValue]],
        *,
        timeout_seconds: float = 60.0,
    ) -> Mapping[str, JsonValue]:
        result = await self.request(
            HOST_DATA_MIGRATE,
            {
                "migrations": [dict(item) for item in migrations],
                "timeout_seconds": timeout_seconds,
            },
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(result, Mapping):
            raise HostCapabilityError("DATA_PROTOCOL_ERROR", "host returned a non-object result")
        return result

    async def request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        if self._closed:
            raise HostCapabilityError("DATA_UNAVAILABLE", "host capability channel is closed")
        budget = timeout_seconds if timeout_seconds is not None else self._default_timeout
        if not 0 < budget <= MAX_TIMEOUT_SECONDS:
            raise HostCapabilityError(
                "DATA_TIMEOUT", "host capability timeout must be positive and bounded"
            )
        request_id = f"hostcall_{uuid.uuid4().hex}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        request = RpcRequest(id=request_id, method=method, params=dict(params))
        encoded = encode_frame(request)
        if len(encoded) > self._max_frame_bytes:
            self._pending.pop(request_id, None)
            raise HostCapabilityError(
                "DATA_PROTOCOL_ERROR", "host capability request exceeds the frame limit"
            )
        try:
            try:
                async with asyncio.timeout(budget):
                    await self._write_frame(encoded)
                    return await future
            except TimeoutError as exc:
                raise HostCapabilityError(
                    "DATA_TIMEOUT", f"host capability call timed out: {method}"
                ) from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    def resolve(self, response: RpcResponse) -> bool:
        """Complete a pending request; return False when the id is unknown."""

        if response.id is None or response.id not in self._pending:
            return False
        future = self._pending[response.id]
        if future.done():
            return True
        if response.error is not None:
            future.set_exception(_capability_error(response.error))
        else:
            future.set_result(response.result)
        return True

    def fail_all(self, error: HostCapabilityError) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)

    def close(self) -> None:
        self._closed = True
        self.fail_all(
            HostCapabilityError("DATA_UNAVAILABLE", "host capability channel is closed")
        )

    async def aclose(self) -> None:
        self.close()


def _capability_error(error: RpcError) -> HostCapabilityError:
    data = error.data if isinstance(error.data, Mapping) else {}
    code = data.get("code")
    retryable = data.get("retryable")
    return HostCapabilityError(
        code if isinstance(code, str) and code else f"HOST_ERROR_{error.code}",
        error.message or "host capability call failed",
        retryable=retryable is True,
    )


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "HOST_DATA_EXECUTE",
    "HOST_DATA_MIGRATE",
    "HOST_DATA_TRANSACTION",
    "MAX_TIMEOUT_SECONDS",
    "HostBroker",
    "HostCapabilityError",
]
