"""The only cooperative path from an Agent plan to a tool side effect."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from personal_assistant.core.approvals.service import (
    ApprovalBinding,
    ApprovalError,
    ApprovalService,
)
from personal_assistant.domain.enums import RiskLevel, ToolOutcomeKind
from personal_assistant.domain.errors import DomainError, ValidationError
from personal_assistant.domain.models import (
    ToolCall,
    ToolDescriptor,
    ToolOutcome,
    utc_now,
)

from .policy import ToolPolicy
from .registry import ToolRegistry
from .result_sanitizer import sanitize_result
from .schemas import validate_json_schema


class ToolRuntimeError(Exception):
    """Base class for typed extension/runtime outcomes."""


class DefinitiveToolFailure(ToolRuntimeError):
    """The executor can prove the intended side effect did not happen."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class OutcomeUnknownError(ToolRuntimeError):
    """The request may have reached the external system."""

    def __init__(self, reference_id: str, message: str = "external outcome is unknown") -> None:
        super().__init__(message)
        self.reference_id = reference_id


class UserActionRequiredError(ToolRuntimeError):
    pass


class ExtensionUnavailableError(ToolRuntimeError):
    def __init__(self, extension_id: str, message: str = "extension is unavailable") -> None:
        super().__init__(message)
        self.extension_id = extension_id


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    attempt_id: str
    task_id: str
    idempotency_key: str
    approval_id: str | None


class ToolExecutor(Protocol):
    async def execute(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class InvocationAuditEvent:
    event_type: str
    attempt_id: str
    task_id: str
    tool_id: str
    tool_version: str
    extension_id: str
    risk: str
    occurred_at: datetime
    detail: Mapping[str, Any] = field(default_factory=dict)


class InvocationAudit(Protocol):
    async def append(self, event: InvocationAuditEvent) -> None: ...


class InMemoryInvocationAudit:
    def __init__(self) -> None:
        self.events: list[InvocationAuditEvent] = []
        self._lock = asyncio.Lock()

    async def append(self, event: InvocationAuditEvent) -> None:
        async with self._lock:
            self.events.append(event)


class ToolGateway:
    """Validate, approve, audit and invoke exactly once per call.

    There is intentionally no retry loop in this class.  In particular, an R2
    timeout or ambiguous exception becomes OUTCOME_UNKNOWN and can only be
    reconciled by a separate read-only workflow or explicit human decision.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: ToolPolicy,
        approvals: ApprovalService,
        executor: ToolExecutor,
        audit: InvocationAudit | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._approvals = approvals
        self._executor = executor
        self._audit = audit or InMemoryInvocationAudit()

    async def invoke(self, call: ToolCall) -> ToolOutcome:
        try:
            descriptor = self._registry.snapshot().resolve(
                call.tool_id, call.tool_version
            )
            validate_json_schema(call.arguments, descriptor.input_schema)
            self._policy.check(descriptor, call)
            binding = self._approval_binding(descriptor, call)
        except DomainError as exc:
            return ToolOutcome.failed(f"{exc.code}: {exc}")

        is_external = descriptor.risk is RiskLevel.EXTERNAL_WRITE
        if is_external and call.approval_id is None:
            assert binding is not None
            approval = await self._approvals.prepare(binding)
            return ToolOutcome(
                ToolOutcomeKind.APPROVAL_REQUIRED,
                approval_id=approval.id,
                reference_id=approval.id,
                message="exact approval is required before this action",
            )

        if is_external:
            assert binding is not None and call.approval_id is not None
            try:
                await self._approvals.consume_for_execution(
                    call.approval_id, binding
                )
            except ApprovalError as exc:
                return ToolOutcome.failed(f"{exc.code}: {exc}")

        attempt_id = str(uuid.uuid4())
        idempotency_key = call.idempotency_key or call.approval_id or attempt_id
        execution_context = ToolExecutionContext(
            attempt_id=attempt_id,
            task_id=call.task_id,
            idempotency_key=idempotency_key,
            approval_id=call.approval_id,
        )
        await self._audit.append(
            InvocationAuditEvent(
                event_type="INTENT_RECORDED",
                attempt_id=attempt_id,
                task_id=call.task_id,
                tool_id=descriptor.id,
                tool_version=descriptor.version,
                extension_id=descriptor.extension_id,
                risk=descriptor.risk.value,
                occurred_at=utc_now(),
                detail={"approval_id": call.approval_id},
            )
        )

        try:
            raw_result = await asyncio.wait_for(
                self._executor.execute(descriptor, call.arguments, execution_context),
                timeout=descriptor.timeout_seconds,
            )
            result = sanitize_result(raw_result)
            validate_json_schema(result, descriptor.output_schema)
        except OutcomeUnknownError as exc:
            return await self._unknown(
                descriptor, call, execution_context, exc.reference_id, str(exc)
            )
        except TimeoutError:
            if is_external:
                reference_id = f"unknown:{attempt_id}"
                return await self._unknown(
                    descriptor,
                    call,
                    execution_context,
                    reference_id,
                    "external action timed out after execution began",
                )
            return await self._failed(
                descriptor,
                call,
                execution_context,
                f"tool timed out after {descriptor.timeout_seconds:g} seconds",
                retryable=True,
            )
        except UserActionRequiredError as exc:
            await self._mark_definitive_external_failure(call, str(exc))
            await self._audit_outcome(
                descriptor, call, execution_context, "USER_ACTION_REQUIRED", str(exc)
            )
            return ToolOutcome(
                ToolOutcomeKind.USER_ACTION_REQUIRED,
                message=str(exc),
                reference_id=attempt_id,
            )
        except ExtensionUnavailableError as exc:
            await self._mark_definitive_external_failure(call, str(exc))
            await self._audit_outcome(
                descriptor, call, execution_context, "EXTENSION_UNAVAILABLE", str(exc)
            )
            return ToolOutcome(
                ToolOutcomeKind.EXTENSION_UNAVAILABLE,
                message=str(exc),
                extension_id=exc.extension_id,
                reference_id=attempt_id,
            )
        except DefinitiveToolFailure as exc:
            await self._mark_definitive_external_failure(call, str(exc))
            return await self._failed(
                descriptor,
                call,
                execution_context,
                str(exc),
                retryable=exc.retryable and not is_external,
            )
        except asyncio.CancelledError:
            if is_external:
                # Persist uncertainty even when the worker itself is being
                # cancelled; shield the state write from that cancellation.
                reference_id = f"unknown:{attempt_id}"
                await asyncio.shield(
                    self._mark_unknown_external(call, reference_id)
                )
            raise
        except Exception as exc:  # noqa: BLE001 - boundary converts untyped worker failures
            if is_external:
                reference_id = f"unknown:{attempt_id}"
                return await self._unknown(
                    descriptor,
                    call,
                    execution_context,
                    reference_id,
                    "executor failed after external execution began; outcome is unknown",
                )
            return await self._failed(
                descriptor,
                call,
                execution_context,
                f"executor error: {type(exc).__name__}",
                retryable=True,
            )

        result_reference = "result:" + hashlib.sha256(
            json.dumps(
                result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        if is_external and call.approval_id is not None:
            await self._approvals.mark_succeeded(
                call.approval_id, result_reference=result_reference
            )
        await self._audit_outcome(
            descriptor, call, execution_context, "SUCCEEDED", result_reference
        )
        return ToolOutcome(
            ToolOutcomeKind.SUCCESS,
            result=result,
            reference_id=result_reference,
        )

    @staticmethod
    def _approval_binding(
        descriptor: ToolDescriptor, call: ToolCall
    ) -> ApprovalBinding | None:
        if descriptor.risk is not RiskLevel.EXTERNAL_WRITE:
            return None
        if call.target is None:
            raise ValidationError("R2 external writes require an explicit target")
        return ApprovalBinding(
            action_type=descriptor.id,
            task_id=call.task_id,
            tool_id=descriptor.id,
            tool_version=descriptor.version,
            extension_id=descriptor.extension_id,
            extension_version=descriptor.extension_version or descriptor.version,
            target=call.target,
            payload=call.arguments,
            attachments=call.attachments,
            form_version=call.form_version,
        )

    async def _unknown(
        self,
        descriptor: ToolDescriptor,
        call: ToolCall,
        context: ToolExecutionContext,
        reference_id: str,
        message: str,
    ) -> ToolOutcome:
        if descriptor.risk is RiskLevel.EXTERNAL_WRITE and call.approval_id is not None:
            await self._mark_unknown_external(call, reference_id)
        await self._audit_outcome(
            descriptor, call, context, "OUTCOME_UNKNOWN", message
        )
        return ToolOutcome.unknown(reference_id, message)

    async def _failed(
        self,
        descriptor: ToolDescriptor,
        call: ToolCall,
        context: ToolExecutionContext,
        message: str,
        *,
        retryable: bool,
    ) -> ToolOutcome:
        await self._audit_outcome(descriptor, call, context, "FAILED", message)
        return ToolOutcome.failed(message, retryable=retryable)

    async def _mark_unknown_external(
        self, call: ToolCall, reference_id: str
    ) -> None:
        if call.approval_id is not None:
            await self._approvals.mark_unknown(
                call.approval_id, reference_id=reference_id
            )

    async def _mark_definitive_external_failure(
        self, call: ToolCall, reason: str
    ) -> None:
        if call.approval_id is not None:
            await self._approvals.mark_failed(call.approval_id, reason=reason)

    async def _audit_outcome(
        self,
        descriptor: ToolDescriptor,
        call: ToolCall,
        context: ToolExecutionContext,
        event_type: str,
        message: str,
    ) -> None:
        await self._audit.append(
            InvocationAuditEvent(
                event_type=event_type,
                attempt_id=context.attempt_id,
                task_id=call.task_id,
                tool_id=descriptor.id,
                tool_version=descriptor.version,
                extension_id=descriptor.extension_id,
                risk=descriptor.risk.value,
                occurred_at=utc_now(),
                detail={"message": message[:500]},
            )
        )
