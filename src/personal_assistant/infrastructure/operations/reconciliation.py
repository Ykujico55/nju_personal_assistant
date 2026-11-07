"""Read authoritative host receipts without dispatching or reusing approvals."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from personal_assistant.core.agent.reconciliation import (
    ReconciledAction,
    reconciliation_binding,
)
from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.core.approvals.service import ApprovalBinding, ApprovalService
from personal_assistant.core.browser.models import BrowserSessionState
from personal_assistant.core.browser.ports import BrowserSessionStore
from personal_assistant.core.jobs.outbox import SideEffectOutboxPort, SideEffectState
from personal_assistant.core.mail import (
    MailDeliveryLedger,
    MailDeliveryRecord,
    MailDeliveryStatus,
    MailRecipientStatus,
)
from personal_assistant.domain import ApprovalState, NotFoundError, ToolCall


class HostReconciliationResolver:
    """Read-only evidence adapter; inputs come from the durable original call.

    Original authorization and outbox outcomes are historical facts. Neither is
    rewritten here. A separate task resolution records the returned binding.
    """

    def __init__(
        self, *, approvals: ApprovalService, outbox: SideEffectOutboxPort,
        mail_ledger: MailDeliveryLedger | None = None,
        browser_sessions: BrowserSessionStore | None = None,
    ) -> None:
        self._approvals = approvals
        self._outbox = outbox
        self._mail = mail_ledger
        self._browser = browser_sessions

    async def resolve(
        self, call: ToolCall, *, extension_id: str, extension_version: str,
        required_capabilities: frozenset[str],
    ) -> ReconciledAction | None:
        if not call.approval_id or not call.idempotency_key or call.target is None:
            return None
        supported = required_capabilities & {"mail.send", "browser.submit"}
        if len(supported) != 1 or not supported <= call.granted_capabilities:
            return None
        if call.tool_id not in call.workflow_allowed_tools:
            return None
        try:
            approval = await self._approvals.get(call.approval_id, refresh_expiry=False)
        except (KeyError, NotFoundError):
            return None
        binding = ApprovalBinding(
            action_type=call.tool_id, task_id=call.task_id, tool_id=call.tool_id,
            tool_version=call.tool_version, extension_id=extension_id,
            extension_version=extension_version, target=call.target, payload=call.arguments,
            attachments=call.attachments, form_version=call.form_version,
        )
        fingerprint = canonical_sha256(binding.envelope())
        if (
            approval.action_fingerprint != fingerprint
            or canonical_sha256(approval.action) != fingerprint
            or approval.consumed_at is None
        ):
            return None
        intent = await self._outbox.get_by_call(call.tool_id, call.idempotency_key)
        if intent is None or (
            intent.task_id != call.task_id or intent.tool_id != call.tool_id
            or intent.idempotency_key != call.idempotency_key
            or intent.approval_id != call.approval_id
            or intent.action_fingerprint != fingerprint
            or intent.canonical_payload_sha256 != canonical_sha256(call.arguments)
        ):
            return None
        valid_pair = (
            approval.state is ApprovalState.UNKNOWN and intent.state is SideEffectState.UNKNOWN
        ) or (
            approval.state is ApprovalState.EXECUTING
            and intent.state in {SideEffectState.PREPARED, SideEffectState.EXECUTING}
        ) or (
            approval.state is ApprovalState.SUCCEEDED and intent.state is SideEffectState.SUCCEEDED
        )
        if not valid_pair:
            return None
        evidence = (
            await self._mail_evidence(call) if "mail.send" in supported
            else await self._browser_evidence(call, extension_id, extension_version)
        )
        if evidence is None:
            return None
        receipt, result = evidence
        receipt = {
            **receipt, "intent_id": intent.id, "approval_id": approval.id,
            "action_fingerprint": fingerprint,
        }
        return ReconciledAction(
            call_binding=reconciliation_binding(
                call, extension_id=extension_id, extension_version=extension_version,
                required_capabilities=required_capabilities,
            ),
            receipt=receipt, result=result,
        )

    async def _mail_evidence(
        self, call: ToolCall,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if self._mail is None:
            return None
        args = call.arguments
        action_id = args.get("local_action_id")
        if not isinstance(action_id, str) or action_id != call.idempotency_key:
            return None
        record = await self._mail.get(action_id)
        if record is None or (
            record.local_action_id != action_id
            or record.account_id != args.get("account_id")
            or record.message_id != args.get("message_id")
            or record.mime_sha256 != args.get("mime_sha256")
            or record.envelope_digest != canonical_sha256(args)
            or record.status is not MailDeliveryStatus.SUCCEEDED
        ):
            return None
        verified_in_sent = record.diagnostic_code == "SENT_RECONCILED"
        if not verified_in_sent and not _confirmed_smtp_receipt(record, args):
            return None
        receipt = {
            "issued_by": "host_mail_ledger", "local_action_id": action_id,
            "message_id": record.message_id, "envelope_digest": record.envelope_digest,
            "mime_sha256": record.mime_sha256, "diagnostic_code": record.diagnostic_code,
            "verification_method": "sent_lookup" if verified_in_sent else "smtp_receipt",
            "server_code": record.server_code,
        }
        # Do not invent a delivery response, body or SMTP per-recipient results.
        return receipt, {
            "local_action_id": action_id, "message_id": record.message_id,
            "status": "SUCCEEDED", "ledger_status": "SUCCEEDED",
            "ledger_recorded": True, "reconciled": True,
        }

    async def _browser_evidence(
        self, call: ToolCall, extension_id: str, extension_version: str,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if self._browser is None:
            return None
        args = call.arguments
        session_id = args.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        record = await self._browser.get(session_id)
        if record is None or (
            record.task_id != call.task_id or record.extension_id != extension_id
            or record.extension_version != extension_version
            or record.state is not BrowserSessionState.SUCCEEDED or record.outcome != "SUCCEEDED"
            or record.diagnostic_code != "RECONCILED" or record.submit_count != 1
            or not record.preview_hash or record.preview_hash != args.get("preview_hash")
            or not record.preview_nonce or record.preview_nonce != args.get("preview_nonce")
            or not isinstance(record.preview, Mapping)
            or record.preview.get("target_action_id") != args.get("action_id")
        ):
            return None
        preview_bindings = {
            "session_id": record.session_id, "canonical_payload_hash": record.preview_hash,
            "nonce": record.preview_nonce, "extension_id": record.extension_id,
            "extension_version": record.extension_version, "transaction_id": record.transaction_id,
            "adapter_id": record.adapter_id, "adapter_version": record.adapter_version,
        }
        if any(record.preview.get(key) != value for key, value in preview_bindings.items()):
            return None
        proof = record.receipt
        if not isinstance(proof, Mapping):
            return None
        reference = proof.get("reference")
        if not isinstance(reference, str) or not reference or (
            proof.get("issued_by") != "host_tracking"
            or proof.get("session_id") != record.session_id
            or proof.get("transaction_id") != record.transaction_id
            or proof.get("adapter_id") != record.adapter_id
            or proof.get("adapter_version") != record.adapter_version
            or proof.get("reference_sha256") != hashlib.sha256(
                reference.encode("utf-8", "replace")
            ).hexdigest()
            or not proof.get("observed_at")
        ):
            return None
        receipt = {key: proof[key] for key in (
            "issued_by", "session_id", "transaction_id", "adapter_id", "adapter_version",
            "reference", "reference_sha256", "observed_at",
        )}
        return receipt, {"state": "SUCCEEDED", "reference": reference, "receipt": receipt.copy()}


def _confirmed_smtp_receipt(record: MailDeliveryRecord, arguments: Mapping[str, Any]) -> bool:
    """Accept only a durable final SMTP 2xx covering every intended recipient.

    This covers a transport success followed by a lost Gateway/runtime receipt;
    attempting Sent reconciliation cannot fix it because that operation only
    transitions UNKNOWN ledger entries. No transport is reopened here.
    """
    code = record.server_code
    if not isinstance(code, str) or len(code) != 3 or not code.isascii() or not code.isdigit():
        return False
    if not 200 <= int(code) < 300:
        return False
    expected: set[str] = set()
    for field in ("to", "cc", "bcc"):
        raw = arguments.get(field, ())
        if raw is None:
            continue
        values = [raw] if isinstance(raw, str) else raw
        if not isinstance(values, (list, tuple)):
            return False
        for value in values:
            if not isinstance(value, str) or not value.strip():
                return False
            expected.add(value.strip().lower())
    results = record.recipient_results
    return bool(expected) and bool(results) and all(
        result.status is MailRecipientStatus.ACCEPTED for result in results
    ) and {result.recipient.strip().lower() for result in results} == expected
