"""Fixed, content-free diagnosis shared by conversation API and terminal UI."""
from __future__ import annotations

import re
from typing import Any

REASONS = {
    "ROUTE_FORMAT_INVALID": "模型返回的意图格式不正确，本轮未进入资料读取或执行。",
    "ROUTE_PROVIDER_UNAVAILABLE": "模型选择的资料提供者不在当前可用清单中，本轮未读取资料。",
    "ROUTE_QUERY_REQUIRED": "资料读取缺少有效查询，本轮未读取资料。",
    "ROUTE_CONTEXT_UNEXPECTED": "普通回答或任务意图夹带了不适用的检索字段，本轮未执行。",
    "TASK_CONTEXT_TOO_LARGE": "创建任务所需的对话上下文过长，请精简后重新提出任务。",
    "CONVERSATION_VALIDATION_FAILED": "对话请求未通过宿主校验，本轮未执行新动作。",
    "ANSWER_CONTENT_INVALID": "完整回复与已接收片段不一致或为空，本轮回复未采用。",
    "MODEL_TIMEOUT": "模型回复超过本次调用时限，未收到完整结果。",
    "MODEL_CONNECTION_FAILED": "模型连接或流读取失败，回复未确认完整。",
    "MODEL_STREAM_INVALID": "模型流缺少完整结束信息或格式不正确。",
    "MODEL_RESPONSE_TOO_LARGE": "模型响应超过传输大小限制，回复未完成。",
    "CONVERSATION_TIMEOUT": "本轮对话达到总处理时限，回复未完成。",
    "RECEIPT_WRITE_FAILED": "对话回执保存未确认，请保留原请求核对。",
    "INTERNAL_FAILURE": "对话处理遇到内部故障，结果未确认。",
    "REQUEST_CANCELLED": "本次请求在完成前被取消，回复未确认完整。",
}
STAGES = {
    "ROUTE": "意图识别", "RETRIEVE": "资料读取", "ANSWER": "回复生成",
    "RECEIPT": "回执保存", "TASK_ENQUEUE": "任务创建",
}
_KEYS = {"reason_code", "stage", "request_id", "elapsed_ms", "persisted"}
_REQUEST = re.compile(r"req_[0-9a-f]{32}")


def parse_diagnostic(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != _KEYS:
        return None
    reason, stage, request_id = value["reason_code"], value["stage"], value["request_id"]
    elapsed, persisted = value["elapsed_ms"], value["persisted"]
    if not isinstance(reason, str) or reason not in REASONS:
        return None
    if not isinstance(stage, str) or stage not in STAGES:
        return None
    if request_id is not None and (
        not isinstance(request_id, str) or _REQUEST.fullmatch(request_id) is None
    ):
        return None
    if type(elapsed) is not int or not 0 <= elapsed <= 86_400_000:
        return None
    if type(persisted) is not bool:
        return None
    return dict(value)


def diagnostic_message(value: object) -> str:
    diagnosis = parse_diagnostic(value)
    if diagnosis is None:
        return ""
    message = (
        f"{REASONS[diagnosis['reason_code']]} "
        f"阶段：{STAGES[diagnosis['stage']]}；耗时 {diagnosis['elapsed_ms'] / 1000:.1f} 秒。"
    )
    if not diagnosis["persisted"]:
        message += " 本次诊断未确认保存，后续查询可能无法取得该原因。"
    return message
