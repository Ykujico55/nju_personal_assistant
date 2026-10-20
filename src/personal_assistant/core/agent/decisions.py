"""Strict model proposals. Authority, task, versions and grants come from the host."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from personal_assistant.core.tools.schemas import validate_json_schema
from personal_assistant.domain import RiskLevel, TaskRun, ToolCall, ToolDescriptor, ValidationError

from .engine import DecisionKind, ValidatedDecision


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("duplicate proposal key")
        result[key] = value
    return result


def parse_proposal(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 128_000:
        raise ValidationError("model proposal is oversized")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, RecursionError):
        raise ValidationError("model proposal must be one strict JSON object") from None
    if not isinstance(value, dict):
        raise ValidationError("model proposal must be an object")
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
            raise ValidationError("proposal must be an object")
        kind = proposal.get("kind")
        if kind == "CALL_TOOL":
            if set(proposal) != {"kind", "tool_id", "tool_version", "arguments"}:
                raise ValidationError("tool proposal cannot supply authority or extra fields")
            tool_id, version = proposal["tool_id"], proposal["tool_version"]
            if not isinstance(tool_id, str) or not isinstance(version, str):
                raise ValidationError("tool identity is invalid")
            descriptor = self._descriptors.get((tool_id, version))
            if descriptor is None or descriptor.risk is RiskLevel.PROHIBITED:
                raise ValidationError("tool/version is not in this run's allowed snapshot")
            arguments = proposal["arguments"]
            if not isinstance(arguments, dict):
                raise ValidationError("tool arguments must be an object")
            validate_json_schema(arguments, descriptor.input_schema)
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
        if kind not in {"COMPLETE", "NEEDS_USER"} or set(proposal) - {"kind", "message", "result"}:
            raise ValidationError("unsupported model decision")
        message = proposal.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 20_000:
            raise ValidationError("a bounded decision message is required")
        return ValidatedDecision(DecisionKind(kind), message=message, result=proposal.get("result"))
