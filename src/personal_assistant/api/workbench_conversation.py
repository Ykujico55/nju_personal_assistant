"""Conversation context reads and durable queue commands; never direct tool dispatch."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import StreamingResponse

from personal_assistant.api.dependencies import get_actor, get_container
from personal_assistant.api.errors import ApplicationError
from personal_assistant.api.workbench_conversation_diagnostics import ConversationDiagnostics
from personal_assistant.bootstrap import Container
from personal_assistant.core.context.extension_provider import ExtensionContextService
from personal_assistant.core.conversation import (
    answer_request,
    conversation_validation_reason,
    parse_route,
    routing_request,
    task_objective,
)
from personal_assistant.core.conversation_diagnostics import parse_diagnostic
from personal_assistant.core.models import ContextField
from personal_assistant.core.models.automatic import authorize_automatic
from personal_assistant.core.models.cleanup import run_cleanup
from personal_assistant.core.models.errors import (
    ModelCredentialUnavailableError,
    ModelOutputFilteredError,
    ModelOutputTruncatedError,
    ModelProviderProtocolError,
    ModelProviderRejectedError,
)
from personal_assistant.core.models.provider import AUDITABLE_USAGE_KEYS, bounded_usage
from personal_assistant.core.records import fingerprint
from personal_assistant.domain import ConcurrentModificationError, ValidationError

router = APIRouter()
BUCKET = "workbench-conversation"
_CONFIRMED_FAILURES: dict[type[Exception], str] = {
    ModelOutputTruncatedError: "MODEL_OUTPUT_TRUNCATED",
    ModelOutputFilteredError: "MODEL_OUTPUT_FILTERED",
    ModelProviderRejectedError: "MODEL_PROVIDER_REJECTED",
    ModelCredentialUnavailableError: "MODEL_CREDENTIAL_UNAVAILABLE",
}
_CONFIRMED_FAILURE_TYPES = tuple(_CONFIRMED_FAILURES)
_FAILURE_MESSAGES = {
    "CHAT_RESPONSE_DISCARDED": (
        "已放弃未完成回复；原请求保留且不会重新调用模型，片段不作为已完成答案。"
    ),
    "MODEL_OUTPUT_TRUNCATED": "回复达到模型输出上限，内容未完成；可缩短问题后重新发送。",
    "MODEL_OUTPUT_FILTERED": "模型明确停止了本次回复，内容未完成；可调整问题后重新发送。",
    "MODEL_PROVIDER_REJECTED": "模型服务明确拒绝了请求；请检查模型配置或额度后重新发送。",
    "MODEL_CREDENTIAL_UNAVAILABLE": "本机模型凭据不可用，当前模型调用未发出；请检查配置。",
}


async def record_model_failure(
    container: Container, key: str, error: Exception, *, version: int,
    diagnostics: ConversationDiagnostics | None = None,
) -> dict[str, Any]:
    # Persist only host-owned codes. Never copy provider text/status bodies or prompts.
    code = next((code for kind, code in _CONFIRMED_FAILURES.items()
                 if isinstance(error, kind)), None)
    if code is None:
        return {"state": "UNKNOWN"}
    value = {"state": "FAILED", "error_code": code}
    if diagnostics is not None:
        diagnostics.stage = "RECEIPT"
    try:
        await container.records.put(BUCKET, key, value, expected_version=version)
    except Exception:
        # A known model failure is not a known durable receipt until it is committed.
        diagnostic = await diagnostics.record(
            reason_code="RECEIPT_WRITE_FAILED", stage="RECEIPT"
        ) if diagnostics else None
        return {"state": "UNKNOWN", "diagnostic": diagnostic}
    return value


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=64000)

    @model_validator(mode="after")
    def bounded_user_message(self) -> Message:
        if self.role == "user" and len(self.content) > 8000:
            raise ValueError("user history message exceeds 8000 characters")
        return self


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
    history: list[Message] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def bounded(self) -> ConversationRequest:
        if (
            not self.message.strip()
            or sum(len(m.content) for m in self.history) + len(self.message) > 64000
        ):
            raise ValueError("bounded nonempty conversation required")
        return self


def sse(event: str, value: dict[str, Any]) -> str:
    return "event: " + event + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n"


def receipt(value: dict[str, Any], diagnostic: object = None) -> str:
    if value.get("state") == "SUCCEEDED":
        return sse("completed", value["response"])
    failed = value.get("state") == "FAILED"
    code = value.get("error_code")
    if failed and isinstance(code, str) and code in _FAILURE_MESSAGES:
        return sse("error", {"code": code, "message": _FAILURE_MESSAGES[code],
                             "uncertain": False})
    diagnosis = parse_diagnostic(diagnostic if diagnostic is not None else value.get("diagnostic"))
    return sse(
        "error",
        {
            **({"diagnostic": diagnosis} if diagnosis else {}),
            "code": "CHAT_RESPONSE_INVALID" if failed else "CHAT_RESULT_UNKNOWN",
            "message": "对话决策无效，未执行新动作。"
            if failed
            else "原请求结果未确认，请保留原请求核对；不会重复调用模型。",
            "uncertain": not failed,
        },
    )


def combined_usage(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    # Only add counters reported by BOTH calls; missing usage must not become zero.
    a, b = bounded_usage(first, AUDITABLE_USAGE_KEYS), bounded_usage(second, AUDITABLE_USAGE_KEYS)
    return {
        key: a[key] + b[key]
        for key in a.keys() & b.keys()
        if type(a[key]) is int and type(b[key]) is int
    }


async def finish_task(
    container: Container, key: str, actor: str, value: dict[str, Any]
) -> dict[str, Any]:
    async def apply() -> dict[str, Any]:
        task = await container.tasks.create(
            objective=value["objective"], idempotency_key=key + ":task", actor=actor
        )
        result = {
            "state": "SUCCEEDED",
            "response": {
                "answer": (
                    f"已创建任务 {task.id}，状态 {task.state.value}，尚未完成。"
                    "可在任务页查看执行进度；最终外部提交仍需审批。"
                ),
                "task": {"id": task.id, "state": task.state.value},
                "model": value["model"],
                "provider": value["provider"],
                "usage": value["usage"],
                "tools_executed": False,
                "sources": [],
                "warnings": [],
            },
        }
        await container.records.put(BUCKET, key, result, expected_version=2)
        return result

    # Production RecordStore shares the TaskService transaction: queued job and
    # response commit together. The task's own key also closes the in-memory gap.
    return await container.records.command(
        key + ":enqueue", {"actor": actor, "objective": value["objective"]}, apply
    )


@router.post("/conversation/discard")
async def discard_conversation_reply(
    payload: ConversationRequest, request: Request,
    container: Container = Depends(get_container), actor: str = Depends(get_actor),
) -> StreamingResponse:
    key = "conversation:" + fingerprint({"actor": actor, "key": request.state.idempotency_key})

    async def missing() -> dict[str, Any]:
        raise ApplicationError("CHAT_DISCARD_NOT_ALLOWED", "原对话命令不存在，不能放弃。", 409)

    # Bind to the original request, without creating or authorizing another command.
    await container.records.command(key, {"actor": actor, **payload.model_dump()}, missing)

    async def body() -> AsyncIterator[str]:
        for _ in range(2):
            record = await container.records.get(BUCKET, key)
            value = record["value"] if record else {}
            if value.get("state") in {"SUCCEEDED", "FAILED"}:
                yield receipt(value)
                return
            if value.get("state") != "STREAMING" or record is None or record["version"] != 2:
                yield sse("error", {"code": "CHAT_DISCARD_NOT_ALLOWED", "uncertain": True,
                    "message": (
                        "只能放弃已进入回复阶段的对话；任务创建或未决动作必须保留原请求核对。"
                    )})
                return
            discarded = {"state": "FAILED", "error_code": "CHAT_RESPONSE_DISCARDED"}
            try:
                await container.records.put(BUCKET, key, discarded, expected_version=2)
            except ConcurrentModificationError:
                # A simultaneous success wins; return its authoritative receipt.
                continue
            except Exception:
                yield receipt({"state": "UNKNOWN"})
                return
            yield receipt(discarded)
            return
        yield receipt({"state": "UNKNOWN"})

    return StreamingResponse(body(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/conversation/stream")
async def conversation_stream(
    payload: ConversationRequest,
    request: Request,
    container: Container = Depends(get_container),
    actor: str = Depends(get_actor),
) -> StreamingResponse:
    if container.settings.model_disclosure_mode != "automatic":
        raise ApplicationError(
            "DISCLOSURE_REQUIRED", "自动对话检索需启用个人资料模型处理；当前可使用普通问答。", 409
        )
    provider = container.settings.model_remote_provider_id
    if container.model_router is None or provider not in container.model_router.provider_ids:
        raise ApplicationError("MODEL_UNAVAILABLE", "模型未配置；请配置后重启后台。", 503)
    key = "conversation:" + fingerprint({"actor": actor, "key": request.state.idempotency_key})
    token = uuid4().hex
    history = [item.model_dump() for item in payload.history]
    diagnostics = ConversationDiagnostics(
        container, key, getattr(request.state, "request_id", None), actor
    )

    async def unknown(error: Exception | None = None, *, reason_code: str | None = None) -> str:
        diagnosis = await diagnostics.record(error, reason_code=reason_code)
        return receipt({"state": "UNKNOWN"}, diagnosis)

    async def timeout_receipt() -> str:
        # Mark the cause before cancelling the pending reader during cleanup.
        diagnostics.total_timeout = True
        if claimed["dispatch_token"] != token and diagnostics.stage != "TASK_ENQUEUE":
            # A status-only query cannot diagnose the original execution.
            return receipt({"state": "UNKNOWN"})
        return await unknown(reason_code="CONVERSATION_TIMEOUT")

    async def record_cancellation() -> None:
        if not diagnostics.total_timeout:
            await run_cleanup(diagnostics.record(reason_code="REQUEST_CANCELLED"))


    async def claim() -> dict[str, Any]:
        await container.records.put(BUCKET, key, {"state": "DISPATCHING"}, expected_version=0)
        return {"dispatch_token": token}

    claimed = await container.records.command(key, {"actor": actor, **payload.model_dump()}, claim)

    async def generate() -> AsyncGenerator[str, None]:
        if claimed["dispatch_token"] != token:
            record = await container.records.get(BUCKET, key)
            value = record["value"] if record else {"state": "UNKNOWN"}
            if value.get("state") == "READY_TASK":
                diagnostics.stage = "TASK_ENQUEUE"
                try:
                    value = await finish_task(container, key, actor, value)
                except Exception as error:
                    yield await unknown(error)
                    return
            diagnosis = (
                await diagnostics.load() if value.get("state") != "SUCCEEDED"
                else None
            )
            yield receipt(value, diagnosis)
            return
        assert container.model_router is not None
        context = ExtensionContextService(container.extension_supervisor)
        try:
            catalog = await context.catalog()
            tools = [
                {"id": t.id, "version": t.version, "risk": t.risk.value}
                for t in container.tool_registry.snapshot().descriptors()
            ][:256]
            route_request = routing_request(payload.message, history, catalog, tools)
            consent = await authorize_automatic(
                route_request,
                provider_id=provider,
                user_id=actor,
                key=key + ":route",
                disclosures=container.disclosures,
                audit=container.audit_writer,
            )
            routed = await container.model_router.complete(
                route_request, provider_id=provider, consent_id=consent, user_id=actor
            )
            route = parse_route(routed.text, catalog, fallback_query=payload.message)
            route_usage = bounded_usage(routed.usage, AUDITABLE_USAGE_KEYS)
            if route.kind == "TASK":
                value = {
                    "state": "READY_TASK",
                    "objective": task_objective(payload.message, history),
                    "model": routed.model_id,
                    "provider": provider,
                    "usage": route_usage,
                }
            else:
                value = {"state": "STREAMING"}
            diagnostics.stage = "RECEIPT"
            await container.records.put(BUCKET, key, value, expected_version=1)
        except _CONFIRMED_FAILURE_TYPES as error:
            yield receipt(await record_model_failure(
                container, key, error, version=1, diagnostics=diagnostics
            ))
            return
        except ValidationError as error:
            reason = conversation_validation_reason(error)
            value = {"state": "FAILED"}
            diagnostics.stage = "RECEIPT"
            try:
                await container.records.put(BUCKET, key, value, expected_version=1)
            except Exception:
                yield await unknown(reason_code="RECEIPT_WRITE_FAILED")
                return
            diagnosis = await diagnostics.record(reason_code=reason, stage="ROUTE")
            yield receipt(value, diagnosis)
            return
        except Exception as error:
            # The dispatched marker is deliberately not cleared on uncertain failure.
            yield await unknown(
                error,
                reason_code="RECEIPT_WRITE_FAILED" if diagnostics.stage == "RECEIPT" else None,
            )
            return
        if value["state"] == "READY_TASK":
            diagnostics.stage = "TASK_ENQUEUE"
            try:
                result = await finish_task(container, key, actor, value)
            except Exception as error:
                yield await unknown(error)
                return
            yield receipt(result)
            return
        sources: list[dict[str, Any]] = []
        warnings: list[str] = []
        fields: tuple[ContextField, ...] = ()
        output = None
        text = ""
        try:
            diagnostics.stage = "RETRIEVE"
            if route.kind == "RETRIEVE":
                retrieved = await context.retrieve(
                    route.query, request_id=key, provider_ids=route.providers
                )
                fields = retrieved.fields
                sources = list(retrieved.citations)
                warnings = list(retrieved.warnings)
                if not fields:
                    warnings.append("本次没有取得可用资料；不代表授权目录为空。")
            diagnostics.stage = "ANSWER"
            model_request = answer_request(
                payload.message,
                history,
                fields=fields,
                retrieved=route.kind == "RETRIEVE",
                warnings=warnings,
            )
            consent = await authorize_automatic(
                model_request,
                provider_id=provider,
                user_id=actor,
                key=key + ":answer",
                disclosures=container.disclosures,
                audit=container.audit_writer,
            )
            stream = container.model_router.stream(
                model_request, provider_id=provider, consent_id=consent, user_id=actor
            )
            try:
                async for event in stream:
                    if output is not None:
                        raise ModelProviderProtocolError("event after completed output")
                    if event.output is not None:
                        output = event.output
                    elif event.delta:
                        text += event.delta
                        yield sse("delta", {"text": event.delta})
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await run_cleanup(close())
            if output is None:
                raise ModelProviderProtocolError("unconfirmed stream")
        except _CONFIRMED_FAILURE_TYPES as error:
            yield receipt(await record_model_failure(
                container, key, error, version=2, diagnostics=diagnostics
            ))
            return
        except Exception as error:
            yield await unknown(error)
            return
        if not output.text.strip() or (text and text != output.text):
            result = {"state": "FAILED"}
        else:
            result = {
                "state": "SUCCEEDED",
                "response": {
                    "answer": output.text,
                    "model": output.model_id,
                    "provider": provider,
                    "usage": combined_usage(route_usage, output.usage),
                    "tools_executed": False,
                    "sources": sources,
                    "warnings": warnings,
                },
            }
        diagnostics.stage = "RECEIPT"
        try:
            await container.records.put(BUCKET, key, result, expected_version=2)
        except Exception:
            yield await unknown(reason_code="RECEIPT_WRITE_FAILED")
            return
        diagnosis = await diagnostics.record(
            reason_code="ANSWER_CONTENT_INVALID", stage="ANSWER"
        ) if result.get("state") == "FAILED" else None
        yield receipt(result, diagnosis)

    async def body() -> AsyncIterator[str]:
        source = generate()
        try:
            async for frame in source:
                yield frame
        except asyncio.CancelledError:
            # A cancelled status-only replay is not a new failure of the original command.
            if claimed["dispatch_token"] == token or diagnostics.stage == "TASK_ENQUEUE":
                await record_cancellation()
            raise
        finally:
            await run_cleanup(source.aclose())

    return StreamingResponse(
        bounded_stream(body(), on_timeout=timeout_receipt),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def bounded_stream(
    source: AsyncIterator[str], *, timeout: float = 660.0, heartbeat: float = 10.0,
    on_timeout: Callable[[], Awaitable[str]] | None = None,
) -> AsyncIterator[str]:
    """One conversation budget, including routing, retrieval and answer streaming."""
    pending: asyncio.Future[str] | None = None
    expired = False
    timeout_frame: str | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                expired = True
                break
            if pending is None:
                pending = asyncio.ensure_future(anext(source))
            done, _ = await asyncio.wait({pending}, timeout=min(heartbeat, remaining))
            if not done:
                if loop.time() >= deadline:
                    expired = True
                    break
                # Never leave an asyncio.timeout context active across yield:
                # it would cancel the caller while the caller consumes this frame.
                yield ": keep-alive\n\n"
                continue
            try:
                frame = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield frame
        if expired and on_timeout is not None:
            timeout_frame = await on_timeout()
    finally:

        async def close() -> None:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            closer = getattr(source, "aclose", None)
            if closer is not None:
                await closer()

        await run_cleanup(close())
    if expired:
        yield timeout_frame or receipt({"state": "UNKNOWN"})
