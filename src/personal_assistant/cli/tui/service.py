"""Async terminal client: loopback only, explicit mutations and exact-request retry."""

from __future__ import annotations

import asyncio
import codecs
import ipaddress
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .model_settings import ModelSettings, ModelSettingsError

PREFIX = "/admin/v1/workbench"
WORKBENCH_PROTOCOL_VERSION = 1
REQUIRED_FEATURES = frozenset({
    "task_queue", "task_trace", "toolset_projection", "configuration_apply",
    "conversation_stream", "conversation_discard", "chat_stream",
})


_ERROR_HINTS = {
    "WORKBENCH_INCOMPATIBLE": "工作台与后台接口不兼容，请重启更新后的后台。",
    "MODEL_CREDENTIAL_UNAVAILABLE": "本机模型凭据不可用，请检查安全凭据句柄。",
    "MODEL_PROVIDER_REJECTED": "模型服务拒绝请求，请检查模型配置、权限或额度。",
    "MODEL_PROVIDER_TIMEOUT": "模型请求超时，请保留原请求并核对结果。",
    "MODEL_PROVIDER_UNAVAILABLE": "模型服务暂不可达，请检查网络和接口地址。",
    "MODEL_PROVIDER_PROTOCOL_ERROR": "模型返回格式不符合接口契约。",
    "MODEL_OUTPUT_TRUNCATED": "模型输出达到服务端上限，本次回复未完成。",
    "MODEL_OUTPUT_FILTERED": "模型服务停止了本次输出。",
    "CHAT_RESULT_UNKNOWN": "回复结果未确认，请重试原请求。",
    "HOST_ALREADY_RUNNING": "此实例已运行；如需加载更新，请使用 restart。",
    "HOST_PORT_IN_USE": "本机端口已占用，请核对运行实例和端口配置。",
    "HOST_CREDENTIAL_UNAVAILABLE": "宿主凭据不可用，请检查安全凭据句柄。",
    "EXTENSION_NOT_ENABLED": "扩展尚未启用或已被隔离，请检查扩展状态。",
    "CONFIG_APPLY_FAILED": "配置未能应用，请修正配置或重启后台。",
    "CONFIG_CHANGED": "配置已变化，请重新读取并核对。",
    "INVALID_EXTENSION_CONFIG": "保存的配置不符合当前扩展Schema，请修正配置。",
    "EXTENSION_VERSION_CHANGED": "扩展版本已变化，请重新读取配置。",
    "REQUIRED_CAPABILITY_UNAVAILABLE": "扩展必需的宿主能力尚不可用，请检查能力配置。",
}


def _error_hint(code: str) -> str:
    return _ERROR_HINTS.get(code, "请求未完成，请使用错误码和关联编号检查本机后台。")


class WorkbenchError(RuntimeError):
    def __init__(self, code: str, message: str = "", *, uncertain: bool = False,
                 correlation_id: str | None = None) -> None:
        self.code = code
        self.message = message or code
        self.uncertain = uncertain
        self.correlation_id = correlation_id if isinstance(correlation_id, str) and re.fullmatch(
            r"(?:req|diag)_[0-9a-f]{32}", correlation_id
        ) else None
        super().__init__(self.message)


def loopback_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
        if (
            not local
            or parsed.scheme != "http"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.port == 0
        ):
            raise ValueError()
    except ValueError:
        raise WorkbenchError(
            "LOCAL_URL_REQUIRED", "管理地址只能使用无凭据的本机 HTTP 回环地址。"
        ) from None
    return value.rstrip("/")


@dataclass(frozen=True)
class _Command:
    method: str
    path: str
    body: bytes
    key: str


class WorkbenchService:
    def __init__(
        self,
        admin_url: str = "http://127.0.0.1:8001",
        *,
        settings_store: ModelSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.admin_url = loopback_url(admin_url)
        self._settings = settings_store
        self._pending: _Command | None = None
        self._stream_delta: Callable[[str], Awaitable[None]] | None = None
        self._mutation_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=self.admin_url,
            timeout=75,
            follow_redirects=False,
            trust_env=False,
            headers={"Accept": "application/json", "X-Assistant-Workbench": "1"},
            transport=transport,
        )

    @property
    def pending(self) -> dict[str, Any] | None:
        command = self._pending
        return (
            {"method": command.method, "path": command.path, "retryable": self._retryable(command)}
            if command
            else None
        )

    @staticmethod
    def _retryable(command: _Command) -> bool:
        return not (command.method == "PUT" and command.path.endswith("/config"))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send(self, command: _Command) -> dict[str, Any]:
        if command.path in {PREFIX + "/chat/stream", PREFIX + "/conversation/stream",
                            PREFIX + "/conversation/discard"}:
            return await self._send_stream(command)
        mutation = command.method != "GET"
        headers = {"Content-Type": "application/json"}
        if mutation:
            headers["Idempotency-Key"] = command.key
        try:
            async with self._client.stream(
                command.method,
                command.path,
                content=command.body or None,
                headers=headers,
            ) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 2_097_152:
                        raise WorkbenchError("RESPONSE_TOO_LARGE", "响应过大。", uncertain=mutation)
                status = response.status_code
                if (status == 404 and command.method == "GET"
                        and command.path == PREFIX + "/capabilities"):
                    raise WorkbenchError(
                        "WORKBENCH_INCOMPATIBLE", _error_hint("WORKBENCH_INCOMPATIBLE")
                    )
                if 300 <= status < 400:
                    raise WorkbenchError("REDIRECT_REFUSED", "管理接口重定向已拒绝。")
                try:
                    body = json.loads(content)
                except (ValueError, UnicodeError):
                    raise WorkbenchError(
                        "INVALID_RESPONSE",
                        "后台返回无效响应。",
                        uncertain=mutation,
                    ) from None
                if not isinstance(body, dict):
                    raise WorkbenchError(
                        "INVALID_RESPONSE", "后台响应格式不正确。", uncertain=mutation
                    )
                if status >= 400:
                    error = body.get("error", {})
                    if not isinstance(error, dict):
                        error = {}
                    code = str(error.get("code", "REQUEST_REJECTED"))
                    uncertain = mutation and (
                        code == "CHAT_RESULT_UNKNOWN"
                        or (status >= 500 and code != "CHAT_RESPONSE_INVALID")
                    )
                    raise WorkbenchError(
                        code,
                        _error_hint(code),
                        uncertain=uncertain,
                        correlation_id=error.get(
                            "request_id", response.headers.get("X-Request-ID")
                        ),
                    )
                return body
        except httpx.HTTPError:
            raise WorkbenchError(
                "CONNECTION_UNAVAILABLE",
                "无法连接本机后台；写入结果未确认时只能重试原请求。",
                uncertain=mutation,
            ) from None

    async def _send_stream(self, command: _Command) -> dict[str, Any]:
        """Read genuine SSE frames, retaining an uncertain command on interruption."""

        request_id: str | None = None

        def bad() -> WorkbenchError:
            return WorkbenchError(
                "STREAM_RESULT_UNCONFIRMED",
                "回复流中断或格式错误；保留原请求核对结果。",
                uncertain=True, correlation_id=request_id,
            )

        try:
            async with asyncio.timeout(90):
                async with self._client.stream(
                    command.method,
                    command.path,
                    content=command.body,
                    headers={
                        "Accept": "text/event-stream",
                        "Content-Type": "application/json",
                        "Idempotency-Key": command.key,
                    },
                ) as response:
                    request_id = response.headers.get("X-Request-ID")
                    if response.status_code != 200:
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > 65536:
                                raise bad()
                        try:
                            error = json.loads(content).get("error", {})
                            code = str(error.get("code", "REQUEST_REJECTED"))
                            message = _error_hint(code)
                        except (ValueError, AttributeError, UnicodeError):
                            raise bad() from None
                        raise WorkbenchError(
                            code,
                            message,
                            uncertain=(
                                response.status_code >= 500 and code != "CHAT_RESPONSE_INVALID"
                            )
                            or code == "CHAT_RESULT_UNKNOWN",
                            correlation_id=error.get("request_id", request_id),
                        )
                    if (
                        response.headers.get("content-type", "").split(";")[0]
                        != "text/event-stream"
                    ):
                        raise bad()
                    decoder = codecs.getincrementaldecoder("utf-8")("strict")
                    buffer, kind, data, text = "", "", [], ""
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 2_097_152:
                            raise bad()
                        buffer += decoder.decode(chunk)
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.removesuffix("\r")
                            if line.startswith(":"):
                                continue
                            if line.startswith("event:"):
                                kind = line[6:].lstrip(" ")
                            elif line.startswith("data:"):
                                data.append(line[5:].lstrip(" "))
                            elif not line:
                                if not data:
                                    kind = ""
                                    continue
                                try:
                                    value = json.loads("\n".join(data))
                                except ValueError:
                                    raise bad() from None
                                data = []
                                if not isinstance(value, dict):
                                    raise bad()
                                if kind == "delta":
                                    delta = value.get("text")
                                    if not isinstance(delta, str):
                                        raise bad()
                                    text += delta
                                    if self._stream_delta:
                                        await self._stream_delta(delta)
                                elif kind == "completed":
                                    answer = value.get("answer")
                                    if (
                                        not isinstance(answer, str)
                                        or not answer.strip()
                                        or (text and answer != text)
                                        or value.get("tools_executed") is not False
                                        or not isinstance(value.get("usage", {}), dict)
                                    ):
                                        raise bad()
                                    if command.path.endswith((
                                        "/conversation/stream", "/conversation/discard",
                                    )):
                                        task = value.get("task")
                                        sources = value.get("sources", [])
                                        warnings = value.get("warnings", [])
                                        if (
                                            (task is not None and (
                                                not isinstance(task, dict)
                                                or not isinstance(task.get("id"), str)
                                                or not task["id"] or len(task["id"]) > 256
                                                or not isinstance(task.get("state"), str)
                                                or not task["state"]
                                            ))
                                            or not isinstance(sources, list)
                                            or len(sources) > 32
                                            or any(not isinstance(item, dict) for item in sources)
                                            or not isinstance(warnings, list)
                                            or len(warnings) > 32
                                            or any(not isinstance(item, str) for item in warnings)
                                        ):
                                            raise bad()
                                    return value
                                elif kind == "error":
                                    stream_code = value.get("code")
                                    uncertain = value.get("uncertain")
                                    if (
                                        not isinstance(stream_code, str)
                                        or type(uncertain) is not bool
                                    ):
                                        raise bad()
                                    raise WorkbenchError(
                                        stream_code,
                                        _error_hint(stream_code),
                                        uncertain=uncertain,
                                        correlation_id=request_id,
                                    )
                                else:
                                    raise bad()
                                kind = ""
                    decoder.decode(b"", final=True)
                    raise bad()
        except (httpx.HTTPError, TimeoutError, UnicodeError):
            raise bad() from None

    async def chat_stream(
        self,
        message: str,
        history: list[dict[str, str]],
        key: str,
        *,
        preview_hash: str | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": message, "history": history}
        if preview_hash is not None:
            payload["preview_hash"] = preview_hash
        return await self._stream_request("/chat/stream", payload, key, on_delta)

    async def conversation_stream(
        self, message: str, history: list[dict[str, str]], key: str,
        *, on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        return await self._stream_request(
            "/conversation/stream", {"message": message, "history": history}, key, on_delta,
        )

    async def _stream_request(
        self, path: str, payload: dict[str, Any], key: str,
        on_delta: Callable[[str], Awaitable[None]] | None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", key):
            raise WorkbenchError("IDEMPOTENCY_KEY_REQUIRED")
        command = _Command(
            "POST",
            PREFIX + path,
            json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
                "utf-8"
            ),
            key,
        )
        if self._pending is not None:
            raise WorkbenchError("RESULT_UNCONFIRMED", "请先核对原请求。", uncertain=True)
        async with self._mutation_lock:
            if self._pending is not None:
                raise WorkbenchError("RESULT_UNCONFIRMED", "请先核对原请求。", uncertain=True)
            self._pending, self._stream_delta = command, on_delta
            return await self._attempt_pending(command)

    async def discard_pending_reply(self) -> dict[str, Any]:
        async with self._mutation_lock:
            original = self._pending
            if original is None or original.path != PREFIX + "/conversation/stream":
                raise WorkbenchError("CHAT_DISCARD_NOT_ALLOWED", "当前请求不是可放弃的对话回复。",
                                     uncertain=original is not None)
            command = _Command("POST", PREFIX + "/conversation/discard",
                               original.body, original.key)
            try:
                result = await self._send(command)
            except WorkbenchError as error:
                confirmed = {"CHAT_RESPONSE_DISCARDED", "CHAT_RESPONSE_INVALID",
                             "MODEL_OUTPUT_TRUNCATED", "MODEL_OUTPUT_FILTERED",
                             "MODEL_PROVIDER_REJECTED", "MODEL_CREDENTIAL_UNAVAILABLE"}
                if error.code in confirmed and not error.uncertain:
                    self._pending, self._stream_delta = None, None
                    raise
                # Retry an uncertain discard through the ORIGINAL receipt only.
                raise WorkbenchError(error.code, error.message, uncertain=True) from None
            self._pending, self._stream_delta = None, None
            return result

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        key: str = "",
    ) -> dict[str, Any]:
        if not path.startswith("/admin/v1/") or "://" in path or "\\" in path:
            raise WorkbenchError("INVALID_ROUTE")
        if method == "GET":
            return await self._send(_Command(method, path, b"", ""))
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", key):
            raise WorkbenchError("IDEMPOTENCY_KEY_REQUIRED")
        command = _Command(
            method,
            path,
            json.dumps(
                payload or {}, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8"),
            key,
        )
        if self._pending is not None:
            raise WorkbenchError("RESULT_UNCONFIRMED", "请先核对原请求。", uncertain=True)
        async with self._mutation_lock:
            if self._pending is not None:
                raise WorkbenchError(
                    "RESULT_UNCONFIRMED", "请先重试原请求，不能提交另一写入。", uncertain=True
                )
            self._pending = command
            return await self._attempt_pending(command)

    async def _attempt_pending(self, command: _Command) -> dict[str, Any]:
        try:
            value = await self._send(command)
        except WorkbenchError as error:
            if not error.uncertain:
                self._pending = None
                self._stream_delta = None
            raise
        self._pending = None
        self._stream_delta = None
        return value

    async def retry_pending(self) -> dict[str, Any]:
        async with self._mutation_lock:
            if self._pending is None:
                raise WorkbenchError("NO_PENDING_COMMAND", "没有待重试请求。")
            if not self._retryable(self._pending):
                raise WorkbenchError(
                    "NO_SAFE_RETRY",
                    "配置接口无幂等回执；请只读核对配置，不能重发原写入。",
                    uncertain=True,
                )
            return await self._attempt_pending(self._pending)

    @staticmethod
    def _id(value: str) -> str:
        if not value or len(value) > 256:
            raise WorkbenchError("INVALID_ID")
        return quote(value, safe="")

    async def negotiate(self) -> dict[str, Any]:
        """Explicit read-only check; it never consumes or retries a pending command."""
        try:
            async with asyncio.timeout(5):
                value = await self._request("GET", PREFIX + "/capabilities")
        except TimeoutError:
            raise WorkbenchError(
                "CONNECTION_UNAVAILABLE", "无法连接本机后台，请检查后台并重新刷新。"
            ) from None
        except WorkbenchError as error:
            if error.code == "INVALID_RESPONSE":
                raise WorkbenchError(
                    "WORKBENCH_INCOMPATIBLE", _error_hint("WORKBENCH_INCOMPATIBLE")
                ) from None
            raise
        protocol = value.get("protocol_version")
        version = value.get("server_version")
        features = value.get("features")
        if (type(protocol) is not int or protocol != WORKBENCH_PROTOCOL_VERSION
                or not isinstance(version, str) or not version.strip() or len(version) > 128
                or not isinstance(features, list)
                or any(not isinstance(item, str) or not item or len(item) > 128
                       for item in features)
                or not REQUIRED_FEATURES.issubset(features)):
            raise WorkbenchError(
                "WORKBENCH_INCOMPATIBLE", _error_hint("WORKBENCH_INCOMPATIBLE")
            )
        return {"protocol_version": protocol, "server_version": version, "features": list(features)}

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", PREFIX + "/system")

    async def usage(self) -> dict[str, Any]:
        return await self._request("GET", PREFIX + "/usage")

    async def tasks(self) -> list[dict[str, Any]]:
        return list((await self._request("GET", PREFIX + "/task-queue"))["items"])

    async def task(self, task_id: str) -> dict[str, Any]:
        return await self._request("GET", PREFIX + "/tasks/" + self._id(task_id))

    async def execution(self, task_id: str) -> dict[str, Any]:
        value = await self._request("GET", PREFIX + "/tasks/" + self._id(task_id) + "/execution")
        value["trace"] = await self._request(
            "GET", PREFIX + "/tasks/" + self._id(task_id) + "/trace"
        )
        return value

    async def create_task(self, objective: str, key: str) -> dict[str, Any]:
        return await self._request("POST", PREFIX + "/tasks", {"objective": objective}, key)

    async def add_message(
        self,
        task_id: str,
        content: str,
        version: int,
        key: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            PREFIX + "/tasks/" + self._id(task_id) + "/messages",
            {"content": content, "version": version},
            key,
        )

    async def resume_task(
        self,
        task_id: str,
        version: int,
        key: str,
        consent_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            PREFIX + "/tasks/" + self._id(task_id) + "/execution",
            {"version": version, "mode": "agent", "consent_id": consent_id},
            key,
        )

    async def move_task(self, task_id: str, job_id: str, version: int,
                        direction: str, key: str) -> dict[str, Any]:
        return await self._request("POST", PREFIX + "/task-queue/" + self._id(task_id) + "/move",
            {"job_id": job_id, "version": version, "direction": direction}, key)

    async def remove_task(self, task_id: str, version: int, key: str) -> dict[str, Any]:
        return await self._request("POST", PREFIX + "/task-queue/" + self._id(task_id) + "/remove",
                                   {"version": version}, key)

    async def cancel_task(self, task_id: str, version: int, key: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            PREFIX + "/tasks/" + self._id(task_id) + "/cancel",
            {"version": version},
            key,
        )

    async def disclosure_preview(
        self,
        task_id: str,
        version: int,
        key: str,
    ) -> dict[str, Any]:
        del version
        return await self._request(
            "POST",
            PREFIX + "/tasks/" + self._id(task_id) + "/model-disclosure/preview",
            {},
            key,
        )

    async def disclosure_confirm(
        self,
        task_id: str,
        version: int,
        preview_hash: str,
        key: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            PREFIX + "/tasks/" + self._id(task_id) + "/model-disclosure",
            {"version": version, "preview_hash": preview_hash},
            key,
        )

    async def chat_preview(
        self,
        message: str,
        history: list[dict[str, str]],
        key: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            PREFIX + "/chat/preview",
            {"message": message, "history": history},
            key,
        )

    async def chat_confirm(
        self,
        message: str,
        history: list[dict[str, str]],
        preview_hash: str,
        key: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            PREFIX + "/chat/confirm",
            {"message": message, "history": history, "preview_hash": preview_hash},
            key,
        )

    async def extensions(self) -> list[dict[str, Any]]:
        return list((await self._request("GET", "/admin/v1/extensions"))["items"])

    async def inspect_extension(self, source: str, key: str) -> dict[str, Any]:
        return await self._request("POST", "/admin/v1/extensions/inspect", {"source": source}, key)

    async def install_extension(self, preview: dict[str, Any], key: str) -> dict[str, Any]:
        body = {name: preview[name] for name in ("plan_id", "confirmation_nonce", "preview_hash")}
        body["accepted_warning"] = True
        path = "/admin/v1/extensions/install"
        if preview.get("mode") == "upgrade":
            path = "/admin/v1/extensions/" + self._id(preview["extension_id"]) + "/upgrade"
        return await self._request("POST", path, body, key)

    async def extension_action(self, extension_id: str, action: str, key: str) -> dict[str, Any]:
        if action not in {"enable", "disable", "uninstall", "rollback"}:
            raise WorkbenchError("INVALID_EXTENSION_ACTION")
        return await self._request(
            "POST",
            "/admin/v1/extensions/" + self._id(extension_id) + "/" + action,
            {},
            key,
        )

    async def extension_config(self, extension_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", "/admin/v1/extensions/" + self._id(extension_id) + "/config"
        )

    async def save_extension_config(
        self,
        extension_id: str,
        config: dict[str, Any],
        key: str,
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            "/admin/v1/extensions/" + self._id(extension_id) + "/config",
            {"config": config},
            key,
        )

    async def apply_extension_config(
        self, extension_id: str, version: str, digest: str, key: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST", "/admin/v1/extensions/" + self._id(extension_id) + "/config/apply",
            {"extension_version": version, "expected_digest": digest}, key,
        )

    async def operation(self, operation_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", "/admin/v1/extension-operations/" + self._id(operation_id)
        )

    async def settings(self) -> dict[str, Any]:
        if self._settings is None:
            raise WorkbenchError("MODEL_CONFIG_UNAVAILABLE", "未选择本机模型配置文件。")
        try:
            return await asyncio.to_thread(self._settings.load)
        except ModelSettingsError as error:
            raise WorkbenchError(error.code) from None

    async def save_settings(self, fields: dict[str, Any], key: str) -> dict[str, Any]:
        if self._pending is not None:
            raise WorkbenchError("RESULT_UNCONFIRMED", "请先处理原请求。", uncertain=True)
        if self._settings is None:
            raise WorkbenchError("MODEL_CONFIG_UNAVAILABLE")
        try:
            return await asyncio.to_thread(self._settings.save, fields, key)
        except ModelSettingsError as error:
            raise WorkbenchError(error.code) from None


WorkbenchClient = WorkbenchService
