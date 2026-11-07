"""Strict model proposals. Authority, task, versions and grants come from the host."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from personal_assistant.core.tools.schemas import SchemaValidationError, validate_json_schema
from personal_assistant.domain import RiskLevel, TaskRun, ToolCall, ToolDescriptor, ValidationError

from .engine import DecisionKind, ValidatedDecision

_VALIDATION_MESSAGES = {
    "PROPOSAL_INVALID_JSON": "模型回复不是单个有效 JSON 对象；本次提案未执行工具，可明确继续重试。",
    "PROPOSAL_INVALID_SHAPE": "模型提案字段不符合约定；本次提案未执行工具，请检查模型提案格式。",
    "PROPOSAL_TOOL_NOT_ALLOWED": (
        "模型选择了本次运行未允许的工具或版本；本次提案未执行工具，请检查工具配置。"
    ),
    "PROPOSAL_INVALID_ARGUMENTS": (
        "模型提供的工具参数不符合 Schema；本次提案未执行工具，请补充输入或明确继续。"
    ),
    "PROPOSAL_INVALID_DECISION": (
        "模型返回了不支持的决策类型；本次提案未执行工具，请检查模型提案格式。"
    ),
    "PROPOSAL_INVALID_MESSAGE": "模型决策缺少有效说明；本次提案未执行工具，可明确继续重试。",
    "HOST_PREPARATION_INVALID": (
        "宿主未能通过动作材料或权限预览校验；尚未派发本次工具，请检查材料和配置。"
    ),
    "HOST_RESOURCE_UNAVAILABLE": "宿主未找到所需资源或能力；请检查材料、工具与扩展配置。",
    "HOST_TOOL_VALIDATION_FAILED": (
        "工具调用阶段未通过校验；请检查动作状态，外部结果未决时必须先对账。"
    ),
    "HOST_WORKFLOW_INVALID": "工作流生成的步骤未通过校验；请检查工作流定义和输入。",
    "HOST_VALIDATION_FAILED": "宿主运行校验未通过；请检查配置和当前执行状态。",
}


def validation_diagnostic(code: object) -> dict[str, str] | None:
    """Project only fixed, safe diagnostics; never trust stored/provider error text."""
    if not isinstance(code, str) or code not in _VALIDATION_MESSAGES:
        return None
    return {"code": code, "message": _VALIDATION_MESSAGES[code]}


class ProposalValidationError(ValidationError):
    def __init__(self, code: str) -> None:
        diagnostic = validation_diagnostic(code)
        if diagnostic is None:
            raise ValueError("unknown proposal diagnostic code")
        self.code = code
        super().__init__(diagnostic["message"])


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProposalValidationError("PROPOSAL_INVALID_JSON")
        result[key] = value
    return result


def parse_proposal(text: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise ProposalValidationError("PROPOSAL_INVALID_JSON")
    try:
        if len(text.encode("utf-8")) > 128_000:
            raise ProposalValidationError("PROPOSAL_INVALID_JSON")
        value = json.loads(
            text,
            object_pairs_hook=_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, RecursionError):
        raise ProposalValidationError("PROPOSAL_INVALID_JSON") from None
    if not isinstance(value, dict):
        raise ProposalValidationError("PROPOSAL_INVALID_SHAPE")
    return value


class DecisionValidator:
    def __init__(
        self,
        descriptors: tuple[ToolDescriptor, ...],
        *,
        grants: frozenset[str] | Mapping[str, frozenset[str]],
    ) -> None:
        self._descriptors = {(d.id, d.version): d for d in descriptors}
        self._allowed = frozenset(d.id for d in descriptors)
        self._grants = grants

    async def validate(self, proposal: Any, run: TaskRun) -> ValidatedDecision:
        if not isinstance(proposal, Mapping):
            raise ProposalValidationError("PROPOSAL_INVALID_SHAPE")
        kind = proposal.get("kind")
        if kind == "CALL_TOOL":
            if set(proposal) != {"kind", "tool_id", "tool_version", "arguments"}:
                raise ProposalValidationError("PROPOSAL_INVALID_SHAPE")
            tool_id, version = proposal["tool_id"], proposal["tool_version"]
            if not isinstance(tool_id, str) or not isinstance(version, str):
                raise ProposalValidationError("PROPOSAL_INVALID_SHAPE")
            descriptor = self._descriptors.get((tool_id, version))
            if descriptor is None or descriptor.risk is RiskLevel.PROHIBITED:
                raise ProposalValidationError("PROPOSAL_TOOL_NOT_ALLOWED")
            arguments = proposal["arguments"]
            if not isinstance(arguments, dict):
                raise ProposalValidationError("PROPOSAL_INVALID_ARGUMENTS")
            try:
                validate_json_schema(arguments, descriptor.input_schema)
            except SchemaValidationError:
                raise ProposalValidationError("PROPOSAL_INVALID_ARGUMENTS") from None
            return ValidatedDecision(
                DecisionKind.CALL_TOOL,
                tool_call=ToolCall(
                    tool_id=tool_id,
                    tool_version=version,
                    arguments=arguments,
                    task_id=run.task_id,
                    granted_capabilities=(
                        self._grants.get(tool_id, frozenset())
                        if isinstance(self._grants, Mapping)
                        else self._grants
                    ),
                    workflow_allowed_tools=self._allowed,
                ),
            )
        if not isinstance(kind, str) or kind not in {"COMPLETE", "NEEDS_USER"}:
            raise ProposalValidationError("PROPOSAL_INVALID_DECISION")
        if set(proposal) - {"kind", "message", "result"}:
            raise ProposalValidationError("PROPOSAL_INVALID_SHAPE")
        message = proposal.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 20_000:
            raise ProposalValidationError("PROPOSAL_INVALID_MESSAGE")
        return ValidatedDecision(DecisionKind(kind), message=message, result=proposal.get("result"))
