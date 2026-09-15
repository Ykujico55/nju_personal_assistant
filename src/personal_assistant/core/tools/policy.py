"""Deterministic workflow, capability and risk checks."""

from __future__ import annotations

from dataclasses import dataclass

from personal_assistant.domain.enums import RiskLevel
from personal_assistant.domain.errors import DomainError
from personal_assistant.domain.models import ToolCall, ToolDescriptor


class ToolPolicyError(DomainError):
    code = "tool_policy_denied"


class ProhibitedToolError(ToolPolicyError):
    code = "prohibited_tool"


@dataclass(slots=True)
class ToolPolicy:
    """Mutable switches are administrative inputs; R3 remains hard-coded."""

    external_writes_enabled: bool = True

    def check(self, descriptor: ToolDescriptor, call: ToolCall) -> None:
        if descriptor.id not in call.workflow_allowed_tools:
            raise ToolPolicyError(f"workflow does not allow tool {descriptor.id}")
        missing = descriptor.required_capabilities - call.granted_capabilities
        if missing:
            raise ToolPolicyError(
                "missing capability grant(s): " + ", ".join(sorted(missing))
            )
        if descriptor.risk is RiskLevel.PROHIBITED:
            # No manifest flag, approval or administrator switch may override R3.
            raise ProhibitedToolError(f"R3 tool is permanently prohibited: {descriptor.id}")
        if descriptor.risk is RiskLevel.EXTERNAL_WRITE and not self.external_writes_enabled:
            raise ToolPolicyError("external side effects are disabled by the kill switch")
