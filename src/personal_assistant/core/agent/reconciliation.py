"""Trusted, read-only reconciliation proofs; never an authorization to send again."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from personal_assistant.core.records import fingerprint
from personal_assistant.domain import ToolCall


@dataclass(frozen=True, slots=True)
class ReconciledAction:
    call_binding: str
    receipt: dict[str, Any]
    result: Any


class ReconciliationPort(Protocol):
    async def resolve(
        self,
        call: ToolCall,
        *,
        extension_id: str,
        extension_version: str,
        required_capabilities: frozenset[str],
    ) -> ReconciledAction | None:
        """Return success only from persisted, exactly bound host evidence.

        This port cannot publish, click, dispatch a tool, or accept a caller-supplied
        outcome. A missing or inconclusive receipt must return None.
        """
        ...


def reconciliation_binding(
    call: ToolCall,
    *,
    extension_id: str,
    extension_version: str,
    required_capabilities: frozenset[str],
) -> str:
    value = asdict(call)
    value["granted_capabilities"] = sorted(call.granted_capabilities)
    value["workflow_allowed_tools"] = sorted(call.workflow_allowed_tools)
    return fingerprint(
        {
            "call": value,
            "extension_id": extension_id,
            "extension_version": extension_version,
            "required_capabilities": sorted(required_capabilities),
        }
    )
