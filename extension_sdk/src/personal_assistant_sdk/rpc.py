"""Versioned JSON-RPC 2.0 framing shared by host and extension workers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

JSONRPC_VERSION = "2.0"


class RpcProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RpcError:
    code: int
    message: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "data": dict(self.data)}


@dataclass(frozen=True, slots=True)
class RpcRequest:
    id: str
    method: str
    params: Mapping[str, Any]
    jsonrpc: str = JSONRPC_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
            "params": dict(self.params),
        }


@dataclass(frozen=True, slots=True)
class RpcResponse:
    id: str | None
    result: Any = None
    error: RpcError | None = None
    jsonrpc: str = JSONRPC_VERSION

    def __post_init__(self) -> None:
        if self.error is not None and self.result is not None:
            raise ValueError("response cannot contain both result and error")

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            body["error"] = self.error.as_dict()
        else:
            body["result"] = self.result
        return body


def encode_frame(message: RpcRequest | RpcResponse | Mapping[str, Any]) -> bytes:
    """Encode one newline-delimited JSON frame."""

    body = message.as_dict() if hasattr(message, "as_dict") else dict(message)
    try:
        return (json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise RpcProtocolError("RPC frame is not JSON serializable") from exc


def decode_request(frame: bytes | str, *, max_bytes: int = 1_048_576) -> RpcRequest:
    body = _decode_json_object(frame, max_bytes=max_bytes)
    if body.get("jsonrpc") != JSONRPC_VERSION:
        raise RpcProtocolError("unsupported jsonrpc version")
    request_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})
    if not isinstance(request_id, str) or not request_id:
        raise RpcProtocolError("request id must be a non-empty string")
    if not isinstance(method, str) or not method:
        raise RpcProtocolError("method must be a non-empty string")
    if not isinstance(params, dict):
        raise RpcProtocolError("params must be an object")
    return RpcRequest(id=request_id, method=method, params=params)


def decode_response(frame: bytes | str, *, max_bytes: int = 1_048_576) -> RpcResponse:
    body = _decode_json_object(frame, max_bytes=max_bytes)
    if body.get("jsonrpc") != JSONRPC_VERSION:
        raise RpcProtocolError("unsupported jsonrpc version")
    response_id = body.get("id")
    if response_id is not None and not isinstance(response_id, str):
        raise RpcProtocolError("response id must be a string or null")
    has_result = "result" in body
    has_error = "error" in body
    if has_result == has_error:
        raise RpcProtocolError("response must contain exactly one of result or error")
    if has_error:
        error = body["error"]
        if not isinstance(error, dict):
            raise RpcProtocolError("error must be an object")
        code = error.get("code")
        message = error.get("message")
        data = error.get("data", {})
        if not isinstance(code, int) or not isinstance(message, str) or not isinstance(data, dict):
            raise RpcProtocolError("invalid error object")
        return RpcResponse(id=response_id, error=RpcError(code, message, data))
    return RpcResponse(id=response_id, result=body["result"])


def _decode_json_object(frame: bytes | str, *, max_bytes: int) -> dict[str, Any]:
    raw = frame.encode("utf-8") if isinstance(frame, str) else frame
    if len(raw) > max_bytes:
        raise RpcProtocolError("RPC frame exceeds configured maximum")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RpcProtocolError("invalid JSON frame") from exc
    if not isinstance(body, dict):
        raise RpcProtocolError("RPC frame must contain a JSON object")
    return body
