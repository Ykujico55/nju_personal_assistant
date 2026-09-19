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

from .models import (
    ArtifactHandle,
    FetchedMail,
    FetchedMailBatch,
    HostArtifactClient,
    HostMailClient,
    JsonValue,
    MailAccountInfo,
    MailboxCapabilities,
    MailFolderInfo,
)
from .rpc import RpcError, RpcRequest, RpcResponse, encode_frame

HOST_DATA_EXECUTE = "host.data.execute"
HOST_DATA_TRANSACTION = "host.data.transaction"
HOST_DATA_MIGRATE = "host.data.migrate"
HOST_MAIL_ACCOUNT = "host.mail.account"
HOST_MAIL_PROBE = "host.mail.probe"
HOST_MAIL_FOLDERS = "host.mail.folders"
HOST_MAIL_FETCH = "host.mail.fetch"
HOST_MAIL_RECONCILE_SENT = "host.mail.reconcile_sent"
HOST_MAIL_DELIVERY_STATUS = "host.mail.delivery_status"
HOST_ARTIFACT_PUT = "host.artifact.put"
HOST_ARTIFACT_READ = "host.artifact.read"
HOST_ARTIFACT_DELETE = "host.artifact.delete"

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

    @property
    def mail(self) -> HostMailClientImpl:
        return HostMailClientImpl(self)

    @property
    def artifact(self) -> HostArtifactClientImpl:
        return HostArtifactClientImpl(self)


class HostMailClientImpl:
    """Read-only mail client over the full-duplex host channel."""

    def __init__(self, broker: HostBroker) -> None:
        self._broker = broker

    async def account(self, account_id: str) -> MailAccountInfo:
        result = await self._broker.request(
            HOST_MAIL_ACCOUNT, {"account_id": account_id}, timeout_seconds=60.0
        )
        return MailAccountInfo(
            account_id=str(_field(result, "account_id")),
            address=str(_field(result, "address")),
            display_name=str(_field(result, "display_name")),
            read_enabled=bool(_field(result, "read_enabled")),
            send_enabled=bool(_field(result, "send_enabled")),
            fingerprint=str(_field(result, "fingerprint")),
        )

    async def probe(self, account_id: str) -> MailboxCapabilities:
        result = await self._broker.request(
            HOST_MAIL_PROBE, {"account_id": account_id}, timeout_seconds=60.0
        )
        return MailboxCapabilities(
            imap_capabilities=_strings(_field(result, "imap_capabilities")),
            auth_mechanisms=_strings(_field(result, "auth_mechanisms")),
            uidvalidity=int(_field(result, "uidvalidity")),
            exists=int(_field(result, "exists")),
        )

    async def list_folders(self, account_id: str) -> tuple[MailFolderInfo, ...]:
        result = await self._broker.request(
            HOST_MAIL_FOLDERS, {"account_id": account_id}, timeout_seconds=60.0
        )
        folders = _field(result, "folders")
        if not isinstance(folders, list):
            raise HostCapabilityError("MAIL_PROTOCOL_ERROR", "folders must be a list")
        return tuple(_folder(item) for item in folders)

    async def fetch(
        self,
        account_id: str,
        folder: str,
        *,
        uidvalidity: int | None,
        start_uid: int,
        limit: int = 100,
    ) -> FetchedMailBatch:
        result = await self._broker.request(
            HOST_MAIL_FETCH,
            {
                "account_id": account_id,
                "folder": folder,
                "uidvalidity": uidvalidity,
                "start_uid": start_uid,
                "limit": limit,
            },
            timeout_seconds=120.0,
        )
        messages = _field(result, "messages")
        if not isinstance(messages, list):
            raise HostCapabilityError("MAIL_PROTOCOL_ERROR", "messages must be a list")
        return FetchedMailBatch(
            uidvalidity=int(_field(result, "uidvalidity")),
            exists=int(_field(result, "exists")),
            messages=tuple(_message(item) for item in messages),
        )

    async def delivery_status(
        self, account_id: str, *, local_action_id: str
    ) -> Mapping[str, JsonValue]:
        result = await self._broker.request(
            HOST_MAIL_DELIVERY_STATUS,
            {"account_id": account_id, "local_action_id": local_action_id},
            timeout_seconds=60.0,
        )
        if not isinstance(result, Mapping):
            raise HostCapabilityError("MAIL_PROTOCOL_ERROR", "host returned a non-object result")
        return result

    async def reconcile_sent(
        self,
        account_id: str,
        *,
        local_action_id: str,
        message_id: str,
        sent_folder: str = "Sent",
    ) -> Mapping[str, JsonValue]:
        result = await self._broker.request(
            HOST_MAIL_RECONCILE_SENT,
            {
                "account_id": account_id,
                "local_action_id": local_action_id,
                "message_id": message_id,
                "sent_folder": sent_folder,
            },
            timeout_seconds=120.0,
        )
        if not isinstance(result, Mapping):
            raise HostCapabilityError("MAIL_PROTOCOL_ERROR", "reconcile result must be an object")
        return result

    async def aclose(self) -> None:
        return None


class HostArtifactClientImpl:
    """Content-addressed host artifact store scoped to this extension."""

    def __init__(self, broker: HostBroker) -> None:
        self._broker = broker

    async def put(
        self, data: bytes, *, media_type: str, sensitivity: str = "PERSONAL"
    ) -> ArtifactHandle:
        import base64

        result = await self._broker.request(
            HOST_ARTIFACT_PUT,
            {
                "data_base64": base64.b64encode(data).decode("ascii"),
                "media_type": media_type,
                "sensitivity": sensitivity,
            },
            timeout_seconds=120.0,
        )
        return ArtifactHandle(
            id=str(_field(result, "id")),
            content_hash=str(_field(result, "content_hash")),
            media_type=str(_field(result, "media_type")),
            size_bytes=int(_field(result, "size_bytes")),
        )

    async def read(self, artifact_id: str) -> bytes:
        import base64

        result = await self._broker.request(
            HOST_ARTIFACT_READ, {"artifact_id": artifact_id}, timeout_seconds=120.0
        )
        data = _field(result, "data_base64")
        if not isinstance(data, str):
            raise HostCapabilityError("ARTIFACT_PROTOCOL_ERROR", "artifact data missing")
        return base64.b64decode(data, validate=True)

    async def delete(self, artifact_id: str) -> None:
        await self._broker.request(
            HOST_ARTIFACT_DELETE, {"artifact_id": artifact_id}, timeout_seconds=60.0
        )

    async def aclose(self) -> None:
        return None


def _field(result: Any, key: str) -> Any:
    if not isinstance(result, Mapping) or key not in result:
        raise HostCapabilityError("MAIL_PROTOCOL_ERROR", f"host response is missing {key}")
    return result[key]


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise HostCapabilityError("MAIL_PROTOCOL_ERROR", "expected a list of strings")
    return tuple(value)


def _folder(value: Any) -> MailFolderInfo:
    if not isinstance(value, Mapping) or not isinstance(value.get("name"), str):
        raise HostCapabilityError("MAIL_PROTOCOL_ERROR", "invalid folder descriptor")
    delimiter = value.get("delimiter")
    return MailFolderInfo(
        name=str(value["name"]),
        delimiter=str(delimiter) if isinstance(delimiter, str) and delimiter else None,
        attributes=_strings(value.get("attributes", [])),
        selectable=bool(value.get("selectable", True)),
    )


def _message(value: Any) -> FetchedMail:
    import base64

    if not isinstance(value, Mapping):
        raise HostCapabilityError("MAIL_PROTOCOL_ERROR", "invalid message descriptor")
    raw_b64 = value.get("raw_base64", "")
    if not isinstance(raw_b64, str):
        raise HostCapabilityError("MAIL_PROTOCOL_ERROR", "invalid message payload")
    message_id = value.get("message_id")
    subject = value.get("subject")
    from_address = value.get("from_address")
    sent_at = value.get("sent_at")
    return FetchedMail(
        uid=int(value.get("uid", 0)),
        message_id=str(message_id) if isinstance(message_id, str) else None,
        subject=str(subject) if isinstance(subject, str) else None,
        from_address=str(from_address) if isinstance(from_address, str) else None,
        to_addresses=_strings(value.get("to_addresses", [])),
        cc_addresses=_strings(value.get("cc_addresses", [])),
        sent_at=str(sent_at) if isinstance(sent_at, str) else None,
        flags=_strings(value.get("flags", [])),
        size_bytes=int(value.get("size_bytes", 0)),
        raw=base64.b64decode(raw_b64, validate=True),
        truncated=bool(value.get("truncated", False)),
    )


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
    "HOST_ARTIFACT_DELETE",
    "HOST_ARTIFACT_PUT",
    "HOST_ARTIFACT_READ",
    "HOST_DATA_EXECUTE",
    "HOST_DATA_MIGRATE",
    "HOST_DATA_TRANSACTION",
    "HOST_MAIL_ACCOUNT",
    "HOST_MAIL_FETCH",
    "HOST_MAIL_DELIVERY_STATUS",
    "HOST_MAIL_FOLDERS",
    "HOST_MAIL_PROBE",
    "HOST_MAIL_RECONCILE_SENT",
    "MAX_TIMEOUT_SECONDS",
    "HostArtifactClient",
    "HostArtifactClientImpl",
    "HostBroker",
    "HostCapabilityError",
    "HostMailClient",
    "HostMailClientImpl",
]
