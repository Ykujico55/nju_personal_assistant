"""Tool Gateway executor for supervised browser actions.

The extension describes *what* to fill; this executor is the only component
that touches the page.  ``browser.fill`` produces the authoritative structured
preview and stops before submission.  ``browser.submit`` is a separate,
separately approved R2 action that clicks exactly one adapter-declared final
action and never retries: a lost response is ``OUTCOME_UNKNOWN``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from personal_assistant.core.browser import (
    AttachmentPreview,
    BrowserError,
    BrowserLimits,
    BrowserSessionBroker,
    FieldChange,
    FieldValidation,
    FieldValueSource,
    FillPlan,
)
from personal_assistant.core.tools.gateway import (
    DefinitiveToolFailure,
    OutcomeUnknownError,
    ToolExecutionContext,
)
from personal_assistant.domain.models import ToolDescriptor

BROWSER_FILL_CAPABILITY = "browser.fill"
BROWSER_SUBMIT_CAPABILITY = "browser.submit"
BROWSER_NAVIGATE_CAPABILITY = "browser.navigate"
BROWSER_ACTION_CAPABILITIES = frozenset(
    {BROWSER_FILL_CAPABILITY, BROWSER_SUBMIT_CAPABILITY, BROWSER_NAVIGATE_CAPABILITY}
)
DEFAULT_CALL_DEADLINE_SECONDS = 180.0


class BrowserActionExecutor:
    def __init__(
        self,
        *,
        broker: BrowserSessionBroker,
        call_deadline_seconds: float = DEFAULT_CALL_DEADLINE_SECONDS,
        limits: BrowserLimits | None = None,
    ) -> None:
        self._broker = broker
        self._deadline = call_deadline_seconds
        self._limits = limits or broker.limits

    async def execute(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Any:
        capabilities = set(descriptor.required_capabilities)
        if BROWSER_FILL_CAPABILITY in capabilities:
            return await self._fill(descriptor, arguments, context)
        if BROWSER_SUBMIT_CAPABILITY in capabilities:
            return await self._submit(descriptor, arguments, context)
        if BROWSER_NAVIGATE_CAPABILITY in capabilities:
            return await self._open(descriptor, arguments, context)
        raise DefinitiveToolFailure("the tool does not require a supervised browser action")

    async def _fill(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        session_id, plan = _fill_plan(arguments, self._limits)
        try:
            preview = await asyncio.wait_for(
                self._broker.execute_fill(
                    session_id,
                    task_id=context.task_id,
                    extension_id=descriptor.extension_id,
                    plan=plan,
                ),
                timeout=self._deadline,
            )
        except TimeoutError as exc:
            # Filling is provably local (mutating requests are blocked), so a
            # timeout is a definitive failure, never UNKNOWN.
            raise DefinitiveToolFailure("BROWSER_TIMEOUT: the fill did not finish") from exc
        except BrowserError as exc:
            raise DefinitiveToolFailure(f"{_code(exc)}: {_message(exc)}") from None
        return preview.to_document()

    async def _open(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        session_id = _text(arguments.get("session_id"), "session_id")
        adapter_id = _text(arguments.get("adapter_id"), "adapter_id")
        transaction_id = _text(arguments.get("transaction_id"), "transaction_id")
        try:
            result = await asyncio.wait_for(
                self._broker.execute_navigation(
                    session_id,
                    extension_id=descriptor.extension_id,
                    adapter_id=adapter_id,
                    transaction_id=transaction_id,
                ),
                timeout=self._deadline,
            )
        except TimeoutError as exc:
            # Opening a transaction is a route/GET step; a timeout is a
            # definitive failure, never UNKNOWN.
            raise DefinitiveToolFailure(
                "BROWSER_TIMEOUT: the navigation action did not finish"
            ) from exc
        except BrowserError as exc:
            raise DefinitiveToolFailure(f"{_code(exc)}: {_message(exc)}") from None
        return dict(result)

    async def _submit(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        session_id = _text(arguments.get("session_id"), "session_id")
        preview_hash = _text(arguments.get("preview_hash"), "preview_hash")
        preview_nonce = _text(arguments.get("preview_nonce"), "preview_nonce")
        action_id = _text(arguments.get("action_id"), "action_id")
        try:
            outcome = await asyncio.wait_for(
                self._broker.execute_submit(
                    session_id,
                    task_id=context.task_id,
                    extension_id=descriptor.extension_id,
                    preview_hash=preview_hash,
                    preview_nonce=preview_nonce,
                    action_id=action_id,
                    owner_id=context.attempt_id,
                ),
                timeout=self._deadline,
            )
        except TimeoutError as exc:
            raise OutcomeUnknownError(
                reference_id=f"browser-submit:{session_id}",
                message="the submission did not finish inside the deadline",
            ) from exc
        except BrowserError as exc:
            raise DefinitiveToolFailure(f"{_code(exc)}: {_message(exc)}") from None
        if outcome.state.value == "UNKNOWN":
            raise OutcomeUnknownError(
                reference_id=f"browser-submit:{session_id}",
                message="the submission outcome is unknown; reconcile read-only",
            )
        if outcome.state.value == "FAILED":
            raise DefinitiveToolFailure(
                f"BROWSER_SUBMIT_FAILED: {outcome.diagnostic_code or 'server rejected'}"
            )
        return {
            "state": outcome.state.value,
            "reference": outcome.reference,
            "receipt": dict(outcome.receipt) if outcome.receipt else None,
        }


def _fill_plan(arguments: Mapping[str, Any], limits: BrowserLimits) -> tuple[str, FillPlan]:
    session_id = _text(arguments.get("session_id"), "session_id")
    fields_raw = arguments.get("fields")
    if not isinstance(fields_raw, Sequence) or isinstance(fields_raw, (str, bytes)):
        raise DefinitiveToolFailure("BROWSER_ARGUMENTS_INVALID: fields must be a list")
    if len(fields_raw) > limits.max_fields:
        raise DefinitiveToolFailure("BROWSER_LIMIT_EXCEEDED: too many fields")
    fields: list[FieldChange] = []
    for item in fields_raw:
        if not isinstance(item, Mapping):
            raise DefinitiveToolFailure("BROWSER_ARGUMENTS_INVALID: a field entry is malformed")
        try:
            source = FieldValueSource(str(item.get("source", "UNKNOWN")))
        except ValueError:
            raise DefinitiveToolFailure(
                "BROWSER_ARGUMENTS_INVALID: a field source is not recognized"
            ) from None
        try:
            validation = FieldValidation(str(item.get("validation", "VALID")))
        except ValueError:
            raise DefinitiveToolFailure(
                "BROWSER_ARGUMENTS_INVALID: a field validation is not recognized"
            ) from None
        evidence = item.get("evidence_sha256")
        try:
            fields.append(
                FieldChange(
                    field_id=_text(item.get("field_id"), "field_id"),
                    locator=_text(item.get("locator"), "locator"),
                    label=_text(item.get("label", ""), "label", max_length=200, allow_empty=True),
                    old_value=_text(item.get("old_value", ""), "old_value", allow_empty=True),
                    new_value=_text(item.get("new_value", ""), "new_value", allow_empty=True),
                    source=source,
                    confidence=float(item.get("confidence", 1.0)),
                    validation=validation,
                    evidence_sha256=str(evidence) if isinstance(evidence, str) else None,
                )
            )
        except (TypeError, ValueError) as exc:
            raise DefinitiveToolFailure(
                "BROWSER_ARGUMENTS_INVALID: a field entry is invalid"
            ) from exc
    attachments_raw = arguments.get("attachments", ())
    if not isinstance(attachments_raw, Sequence) or isinstance(attachments_raw, (str, bytes)):
        raise DefinitiveToolFailure("BROWSER_ARGUMENTS_INVALID: attachments must be a list")
    if len(attachments_raw) > limits.max_attachments:
        raise DefinitiveToolFailure("BROWSER_LIMIT_EXCEEDED: too many attachments")
    attachments: list[AttachmentPreview] = []
    for item in attachments_raw:
        if not isinstance(item, Mapping):
            raise DefinitiveToolFailure("BROWSER_ARGUMENTS_INVALID: an attachment is malformed")
        try:
            attachments.append(
                AttachmentPreview(
                    name=_text(item.get("name"), "attachment name", max_length=255),
                    size_bytes=int(item.get("size_bytes", -1)),
                    sha256=_text(item.get("sha256"), "attachment sha256", max_length=64),
                    media_type=_text(
                        item.get("media_type", "application/octet-stream"),
                        "media type",
                        max_length=128,
                    ),
                )
            )
        except (TypeError, ValueError) as exc:
            raise DefinitiveToolFailure(
                "BROWSER_ARGUMENTS_INVALID: an attachment is invalid"
            ) from exc
    try:
        plan = FillPlan(
            adapter_id=_text(arguments.get("adapter_id"), "adapter_id"),
            adapter_version=_text(arguments.get("adapter_version"), "adapter_version"),
            transaction_id=_text(arguments.get("transaction_id"), "transaction_id"),
            expected_origin=_text(arguments.get("expected_origin"), "expected_origin"),
            expected_page_fingerprint=_text(
                arguments.get("expected_page_fingerprint"), "expected_page_fingerprint"
            ),
            app_id=_text(arguments.get("app_id", ""), "app_id", allow_empty=True),
            consequences=_text(
                arguments.get("consequences", ""),
                "consequences",
                max_length=2000,
                allow_empty=True,
            ),
            fields=tuple(fields),
            attachments=tuple(attachments),
        )
    except (TypeError, ValueError) as exc:
        raise DefinitiveToolFailure("BROWSER_ARGUMENTS_INVALID: the fill plan is invalid") from exc
    return session_id, plan


def _text(
    value: Any,
    name: str,
    *,
    max_length: int = 2048,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise DefinitiveToolFailure(f"BROWSER_ARGUMENTS_INVALID: {name} must be a string")
    if len(value) > max_length:
        raise DefinitiveToolFailure(f"BROWSER_ARGUMENTS_INVALID: {name} is too long")
    if not value and not allow_empty:
        raise DefinitiveToolFailure(f"BROWSER_ARGUMENTS_INVALID: {name} is required")
    return value


def _code(exc: Exception) -> str:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, str) and reason:
        return reason
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return "BROWSER_ERROR"


def _message(exc: Exception) -> str:
    text = str(exc)
    return text[:300] if text else "the browser action failed"


__all__ = [
    "BROWSER_ACTION_CAPABILITIES",
    "BROWSER_FILL_CAPABILITY",
    "BROWSER_NAVIGATE_CAPABILITY",
    "BROWSER_SUBMIT_CAPABILITY",
    "DEFAULT_CALL_DEADLINE_SECONDS",
    "BrowserActionExecutor",
]
