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
from personal_assistant.domain import RiskLevel
from personal_assistant.domain.models import ToolDescriptor
from personal_assistant.infrastructure.browser.executor import (
    BROWSER_ACTION_CAPABILITIES,
    BrowserActionExecutor,
)
from personal_assistant.infrastructure.mail.executor import (
    MAIL_SEND_CAPABILITY,
    ExtensionToolInvoker,
    MailSendExecutor,
)

from .extension_actions import ExtensionActionService


class CapabilityRoutingExecutor(ToolExecutor):
    def __init__(
        self,
        *,
        extension_router: ExtensionToolInvoker,
        mail_send: MailSendExecutor | None = None,
        browser_actions: BrowserActionExecutor | None = None,
        extension_actions: ExtensionActionService | None = None,
    ) -> None:
        self._extension_router = extension_router
        self._mail_send = mail_send
        self._browser_actions = browser_actions
        self._extension_actions = extension_actions

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
        if descriptor.required_capabilities & BROWSER_ACTION_CAPABILITIES:
            if self._browser_actions is None:
                raise DefinitiveToolFailure(
                    "the supervised browser capability is not configured on this host"
                )
            return await self._browser_actions.execute(descriptor, arguments, context)
        if descriptor.risk is RiskLevel.EXTERNAL_WRITE:
            if self._extension_actions is None:
                raise DefinitiveToolFailure("extension action review is unavailable")
            await self._extension_actions.review(descriptor, arguments, task_id=context.task_id)
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
            if descriptor.risk is RiskLevel.EXTERNAL_WRITE:
                raise OutcomeUnknownError(
                    context.idempotency_key,
                    "external worker receipt lost",
                ) from None
            raise DefinitiveToolFailure(
                "the extension worker is unavailable or failed to execute"
            ) from None
        if not isinstance(result, Mapping):
            raise OutcomeUnknownError(context.idempotency_key, "extension receipt is invalid")
        outcome = result.get("outcome")
        if outcome == "SUCCEEDED":
            receipt = result.get("external_receipt")
            if descriptor.risk is RiskLevel.EXTERNAL_WRITE and (
                not isinstance(receipt, Mapping)
                or receipt.get("verified") is not True
                or not isinstance(receipt.get("reference_id"), str)
                or not receipt["reference_id"]
            ):
                raise OutcomeUnknownError(
                    context.idempotency_key,
                    "external receipt is unconfirmed",
                )
            output = result.get("output")
            return dict(output) if isinstance(output, Mapping) else {}
        if descriptor.risk is RiskLevel.EXTERNAL_WRITE:
            # A worker's ordinary error code is not evidence that dispatch never happened.
            raise OutcomeUnknownError(
                context.idempotency_key,
                "external worker outcome is unconfirmed",
            )
        if outcome == "NEEDS_USER_ACTION":
            raise UserActionRequiredError(
                str(result.get("message") or "the extension requires user action")
            )
        if outcome == "UNKNOWN":
            raise OutcomeUnknownError(
                context.attempt_id,
                "the extension reported an unknown outcome",
            )
        raise DefinitiveToolFailure("the extension rejected the tool call")


__all__ = ["CapabilityRoutingExecutor"]
