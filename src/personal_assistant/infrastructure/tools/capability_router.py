"""Generic capability-routing ToolExecutor.

The gateway has exactly one executor.  This router dispatches a call either to
a host capability executor (currently the mail-send executor, selected by the
``mail.send`` required capability) or to the owning extension worker.  The
routing decision comes from the descriptor's declared capabilities, never from
a business-extension id.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from personal_assistant.core.tools.gateway import (
    DefinitiveToolFailure,
    OutcomeUnknownError,
    ToolExecutionContext,
    ToolExecutor,
    UserActionRequiredError,
)
from personal_assistant.domain.models import ToolDescriptor
from personal_assistant.infrastructure.mail.executor import (
    MAIL_SEND_CAPABILITY,
    ExtensionToolInvoker,
    MailSendExecutor,
)


class CapabilityRoutingExecutor(ToolExecutor):
    def __init__(
        self,
        *,
        extension_router: ExtensionToolInvoker,
        mail_send: MailSendExecutor | None = None,
    ) -> None:
        self._extension_router = extension_router
        self._mail_send = mail_send

    async def execute(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Any:
        if MAIL_SEND_CAPABILITY in descriptor.required_capabilities:
            if self._mail_send is None:
                raise DefinitiveToolFailure(
                    "the mail send capability is not configured on this host"
                )
            return await self._mail_send.execute(descriptor, arguments, context)
        return await self._invoke_extension(descriptor, arguments, context)

    async def _invoke_extension(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Any:
        try:
            result = await self._extension_router.invoke_tool(
                descriptor.extension_id,
                descriptor.id,
                arguments,
                task_id=context.task_id,
                run_id=context.attempt_id,
                idempotency_key=context.idempotency_key,
                deadline_seconds=descriptor.timeout_seconds,
            )
        except Exception:  # noqa: BLE001 - worker failure is typed at the boundary
            raise DefinitiveToolFailure(
                "the extension worker is unavailable or failed to execute"
            ) from None
        outcome = result.get("outcome")
        if outcome == "SUCCEEDED":
            output = result.get("output")
            return dict(output) if isinstance(output, Mapping) else {}
        if outcome == "NEEDS_USER_ACTION":
            raise UserActionRequiredError(
                str(result.get("message") or "the extension requires user action")
            )
        if outcome == "UNKNOWN":
            raise OutcomeUnknownError(
                context.attempt_id,
                "the extension reported an unknown outcome",
            )
        raise DefinitiveToolFailure(
            "the extension rejected the tool call"
        )


__all__ = ["CapabilityRoutingExecutor"]
