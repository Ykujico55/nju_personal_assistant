"""F06 unit tests: R2 send path through the Tool Gateway and SideEffect Outbox."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.mail import (
    MailAccountRecord,
    MailDeliveryReceipt,
    MailDeliveryRecord,
    MailDeliveryStatus,
    MailError,
    MailErrorCode,
    MailLedgerConflictError,
    MailLedgerStateError,
    MailPolicy,
    MailRecipientResult,
    MailRecipientStatus,
)
from personal_assistant.core.tools import ToolGateway, ToolPolicy, ToolRegistry
from personal_assistant.domain import (
    ApprovalState,
    RiskLevel,
    ToolCall,
    ToolDescriptor,
    ToolOutcomeKind,
)
from personal_assistant.infrastructure.mail.executor import MailSendExecutor
from personal_assistant.infrastructure.mail.mime import build_message_bytes
from personal_assistant.infrastructure.mail.registry import InMemoryMailAccountRegistry
from personal_assistant.infrastructure.memory import InMemorySideEffectOutbox

LOCAL_ACTION = "act_" + "a" * 40
MESSAGE_ID = f"<smail.{LOCAL_ACTION}@example.test>"
RECIPIENTS = ("good@example.test", "second@example.test")
ACCOUNT = MailAccountRecord(
    account_id="nju",
    address="student@smail.nju.edu.cn",
    imap_host="imap.example.test",
    smtp_host="smtp.example.test",
    secret_handle_id="smail-handle-1",
    send_enabled=True,
)
FINGERPRINT = ACCOUNT.fingerprint()


class FakeArtifacts:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def put(self, artifact_id: str, data: bytes) -> str:
        self.blobs[artifact_id] = data
        return hashlib.sha256(data).hexdigest()

    async def read_as_host(self, artifact_id: str) -> bytes:
        return self.blobs[artifact_id]


class FakeInvoker:
    def __init__(self, artifacts: FakeArtifacts) -> None:
        self.artifacts = artifacts
        self.calls = 0
        self.force_permanent = False

    async def invoke_tool(
        self,
        extension_id: str,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        task_id: str,
        run_id: str,
        idempotency_key: str,
        deadline_seconds: float,
    ) -> dict[str, Any]:
        del extension_id, tool_id, task_id, run_id, deadline_seconds
        self.calls += 1
        if self.force_permanent:
            return {"outcome": "PERMANENT", "output": {"code": "SMAL_DRAFT_CHANGED"}}
        artifact_id = arguments["mime_artifact_id"]
        return {
            "outcome": "SUCCEEDED",
            "output": {
                "mime_artifact_id": artifact_id,
                "mime_sha256": arguments["mime_sha256"],
                "to": arguments["to"],
                "cc": arguments.get("cc", []),
                "bcc": arguments.get("bcc", []),
                "subject": arguments["subject"],
                "attachment_hashes": arguments.get("attachment_hashes", []),
            },
        }


class FakeBroker:
    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.status = MailDeliveryStatus.SUCCEEDED
        self.raise_error: MailError | None = None

    @property
    def send_available(self) -> bool:
        return True

    async def send(
        self, account_id: Any, request: Any, *, guard: Any = None
    ) -> MailDeliveryReceipt:
        del account_id, guard
        if self.raise_error is not None:
            raise self.raise_error
        self.sent.append(request)
        results = tuple(
            MailRecipientResult(recipient=item, status=MailRecipientStatus.ACCEPTED)
            for item in request.recipients()
        )
        if self.status is MailDeliveryStatus.PARTIAL:
            results = (
                MailRecipientResult(
                    recipient=request.recipients()[0], status=MailRecipientStatus.ACCEPTED
                ),
                MailRecipientResult(
                    recipient=request.recipients()[-1],
                    status=MailRecipientStatus.REJECTED,
                    error_code="SMTP_550",
                ),
            )
        if self.status is MailDeliveryStatus.UNKNOWN:
            results = tuple(
                MailRecipientResult(recipient=item, status=MailRecipientStatus.UNKNOWN)
                for item in request.recipients()
            )
        return MailDeliveryReceipt(
            local_action_id=request.local_action_id,
            message_id=request.message_id,
            status=self.status,
            recipient_results=results,
            server_code="250",
        )


class FakeLedger:
    """In-memory copy of the production state machine with failure injection."""

    def __init__(self) -> None:
        self.records: dict[str, MailDeliveryRecord] = {}
        self.fail_finalize = False
        self.transitions: list[str] = []

    async def prepare(self, record: MailDeliveryRecord) -> MailDeliveryStatus:
        from dataclasses import replace

        existing = self.records.get(record.local_action_id)
        if existing is None:
            self.records[record.local_action_id] = replace(
                record, status=MailDeliveryStatus.PREPARED
            )
            self.transitions.append("PREPARED")
            return MailDeliveryStatus.PREPARED
        if existing.envelope_digest != record.envelope_digest:
            raise MailLedgerConflictError("different envelope")
        return existing.status

    async def begin_execution(
        self,
        local_action_id: str,
        envelope_digest: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> MailDeliveryStatus:
        from dataclasses import replace

        record = self.records[local_action_id]
        if record.envelope_digest != envelope_digest:
            raise MailLedgerConflictError("different envelope")
        if record.status is MailDeliveryStatus.PREPARED:
            self.records[local_action_id] = replace(
                record,
                status=MailDeliveryStatus.EXECUTING,
                owner_id=owner_id,
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds),
            )
            self.transitions.append("EXECUTING")
            return MailDeliveryStatus.EXECUTING
        return record.status

    async def finalize(
        self,
        local_action_id: str,
        envelope_digest: str,
        *,
        status: MailDeliveryStatus,
        recipient_results: tuple[MailRecipientResult, ...] = (),
        server_code: str | None = None,
        diagnostic_code: str | None = None,
    ) -> MailDeliveryStatus:
        from dataclasses import replace

        if self.fail_finalize:
            raise MailLedgerStateError("injected finalize failure")
        record = self.records[local_action_id]
        if record.status.terminal:
            return record.status
        self.records[local_action_id] = replace(
            record,
            status=status,
            recipient_results=recipient_results,
            server_code=server_code,
            diagnostic_code=diagnostic_code,
        )
        self.transitions.append(status.value)
        return status

    async def reconcile(
        self,
        local_action_id: str,
        *,
        account_id: str,
        message_id: str,
        status: MailDeliveryStatus,
        diagnostic_code: str | None = None,
    ) -> MailDeliveryStatus:
        from dataclasses import replace

        record = self.records[local_action_id]
        if (
            record.status is MailDeliveryStatus.UNKNOWN
            and record.account_id == account_id
            and record.message_id == message_id
        ):
            self.records[local_action_id] = replace(
                record, status=status, server_code="SENT_RECONCILED"
            )
            return status
        return record.status

    async def heartbeat(
        self, local_action_id: str, owner_id: str, *, lease_seconds: float
    ) -> bool:
        record = self.records.get(local_action_id)
        return not (
            record is None
            or record.status is not MailDeliveryStatus.EXECUTING
            or record.owner_id != owner_id
        )

    async def recover_stale_executions(
        self, *, active_owners: Any = ()
    ) -> tuple[str, ...]:
        return ()

    async def get(self, local_action_id: str) -> MailDeliveryRecord | None:
        return self.records.get(local_action_id)

    async def close(self) -> None:
        return None


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        id="smail.send",
        version="1",
        extension_id="nju.smail",
        extension_version="0.1.0",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        risk=RiskLevel.EXTERNAL_WRITE,
        required_capabilities=frozenset({"mail.send"}),
    )


def send_payload(
    *, body: str = "Body", local_action_id: str = LOCAL_ACTION
) -> tuple[dict[str, Any], bytes]:
    message_id = f"<smail.{local_action_id}@example.test>"
    raw = build_message_bytes(
        message_id=message_id,
        from_address="student@smail.nju.edu.cn",
        to_addresses=(RECIPIENTS[0],),
        cc_addresses=(),
        bcc_addresses=(RECIPIENTS[1],),
        subject="Hello",
        body_text=body,
    )
    digest = hashlib.sha256(raw).hexdigest()
    arguments = {
        "account_id": ACCOUNT.account_id,
        "account_fingerprint": FINGERPRINT,
        "draft_id": "draft-1",
        "draft_version": 1,
        "canonical_digest": "c" * 64,
        "local_action_id": local_action_id,
        "message_id": message_id,
        "from_address": "student@smail.nju.edu.cn",
        "to": [RECIPIENTS[0]],
        "cc": [],
        "bcc": [RECIPIENTS[1]],
        "subject": "Hello",
        "mime_sha256": digest,
        "mime_artifact_id": f"art-{digest[:8]}",
        "attachment_hashes": [],
    }
    return arguments, raw


def call(
    arguments: dict[str, Any], *, approval_id: str | None, idempotency_key: str | None
) -> ToolCall:
    return ToolCall(
        tool_id="smail.send",
        tool_version="1",
        task_id="task-1",
        arguments=arguments,
        target={"account_id": "nju"},
        workflow_allowed_tools=frozenset({"smail.send"}),
        granted_capabilities=frozenset({"mail.send"}),
        approval_id=approval_id,
        idempotency_key=idempotency_key,
    )


class MailSendGatewayTests(unittest.IsolatedAsyncioTestCase):
    def make_gateway(self) -> tuple[ToolGateway, ApprovalService]:
        registry = ToolRegistry()
        registry.publish((descriptor(),))
        approvals = ApprovalService(InMemoryApprovalRepository())
        gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=approvals,
            executor=self.executor,
            outbox=InMemorySideEffectOutbox(approvals),
        )
        return gateway, approvals

    async def asyncSetUp(self) -> None:
        self.artifacts = FakeArtifacts()
        self.invoker = FakeInvoker(self.artifacts)
        self.broker = FakeBroker()
        self.ledger = FakeLedger()
        self.accounts = InMemoryMailAccountRegistry((ACCOUNT,))
        self.executor = MailSendExecutor(
            broker=self.broker,  # type: ignore[arg-type]
            artifacts=self.artifacts,
            invoker=self.invoker,
            ledger=self.ledger,  # type: ignore[arg-type]
            accounts=self.accounts,
            policy=MailPolicy(
                allow_send=True, allowed_recipients=frozenset(a.lower() for a in RECIPIENTS)
            ),
        )
        self.gateway, self.approvals = self.make_gateway()

    async def _approve(self, arguments: dict[str, Any]) -> str:
        needed = await self.gateway.invoke(
            call(arguments, approval_id=None, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.APPROVAL_REQUIRED, needed.kind)
        prepared = await self.approvals.get(needed.approval_id or "")
        await self.approvals.approve(prepared.id, nonce=prepared.nonce, actor_id="owner")
        return prepared.id

    async def test_no_approval_never_reaches_the_transport_or_ledger(self) -> None:
        arguments, raw = send_payload()
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=None, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.APPROVAL_REQUIRED, outcome.kind)
        self.assertEqual(0, self.invoker.calls)
        self.assertEqual([], self.broker.sent)
        self.assertEqual({}, self.ledger.records)

    async def test_approved_send_durably_executes_then_transmits(self) -> None:
        arguments, raw = send_payload()
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.SUCCESS, outcome.kind)
        self.assertEqual(["PREPARED", "EXECUTING", "SUCCEEDED"], self.ledger.transitions)
        self.assertEqual(1, len(self.broker.sent))
        self.assertEqual(raw, self.broker.sent[0].mime_bytes)
        final = await self.approvals.get(approval_id)
        self.assertEqual(ApprovalState.SUCCEEDED, final.state)

    async def test_ledger_finalize_failure_is_unknown_not_success(self) -> None:
        arguments, raw = send_payload()
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        self.ledger.fail_finalize = True
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.OUTCOME_UNKNOWN, outcome.kind)
        self.assertFalse(outcome.retryable)
        self.assertEqual(1, len(self.broker.sent))
        final = await self.approvals.get(approval_id)
        self.assertEqual(ApprovalState.UNKNOWN, final.state)

    async def test_interrupted_execution_never_resends(self) -> None:
        arguments, raw = send_payload()
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        from dataclasses import replace

        await self.ledger.prepare(
            MailDeliveryRecord(
                local_action_id=LOCAL_ACTION,
                account_id="nju",
                message_id=arguments["message_id"],
                status=MailDeliveryStatus.PREPARED,
                envelope_digest="c" * 64,
                mime_sha256=arguments["mime_sha256"],
            )
        )
        # Force the digest to match the approved arguments.
        from personal_assistant.core.approvals.canonicalize import canonical_sha256

        digest = canonical_sha256(dict(arguments))
        self.ledger.records[LOCAL_ACTION] = replace(
            self.ledger.records[LOCAL_ACTION],
            envelope_digest=digest,
            status=MailDeliveryStatus.EXECUTING,
        )
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.OUTCOME_UNKNOWN, outcome.kind)
        self.assertEqual([], self.broker.sent)
        # Materialization has no external side effect; the transport is never
        # retried after an interrupted EXECUTING record.
        self.assertEqual(1, self.invoker.calls)

    async def test_conflicting_envelope_for_same_action_id_is_rejected(self) -> None:
        arguments, raw = send_payload()
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        from personal_assistant.core.approvals.canonicalize import canonical_sha256

        await self.ledger.prepare(
            MailDeliveryRecord(
                local_action_id=LOCAL_ACTION,
                account_id="nju",
                message_id=arguments["message_id"],
                status=MailDeliveryStatus.PREPARED,
                envelope_digest="0" * 64,
                mime_sha256=arguments["mime_sha256"],
            )
        )
        del canonical_sha256
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.FAILED, outcome.kind)
        self.assertEqual([], self.broker.sent)

    async def test_account_not_registered_never_reaches_materialization(self) -> None:
        arguments, raw = send_payload()
        arguments["account_id"] = "unknown"
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.FAILED, outcome.kind)
        self.assertEqual(0, self.invoker.calls)
        self.assertEqual([], self.broker.sent)

    async def test_modified_payload_with_old_approval_is_rejected(self) -> None:
        arguments, raw = send_payload(body="original")
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        edited, edited_raw = send_payload(body="edited")
        self.artifacts.blobs[edited["mime_artifact_id"]] = edited_raw
        outcome = await self.gateway.invoke(
            call(edited, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertNotEqual(ToolOutcomeKind.SUCCESS, outcome.kind)
        self.assertEqual(0, self.invoker.calls)
        self.assertEqual([], self.broker.sent)
        final = await self.approvals.get(approval_id)
        self.assertEqual(ApprovalState.CANCELLED, final.state)

    async def test_stale_draft_version_never_reaches_the_transport(self) -> None:
        arguments, raw = send_payload()
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        self.invoker.force_permanent = True
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.FAILED, outcome.kind)
        self.assertEqual([], self.broker.sent)
        self.assertEqual({}, self.ledger.records)

    async def test_partial_delivery_is_never_reported_as_success(self) -> None:
        arguments, raw = send_payload()
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        self.broker.status = MailDeliveryStatus.PARTIAL
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.FAILED, outcome.kind)
        record = self.ledger.records[LOCAL_ACTION]
        self.assertEqual(MailDeliveryStatus.PARTIAL, record.status)
        self.assertEqual(2, len(record.recipient_results))

    async def test_unknown_outcome_is_not_retryable_and_not_retried(self) -> None:
        arguments, raw = send_payload()
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        self.broker.status = MailDeliveryStatus.UNKNOWN
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.OUTCOME_UNKNOWN, outcome.kind)
        self.assertFalse(outcome.retryable)
        self.assertEqual(
            MailDeliveryStatus.UNKNOWN, self.ledger.records[LOCAL_ACTION].status
        )
        final = await self.approvals.get(approval_id)
        self.assertEqual(ApprovalState.UNKNOWN, final.state)
        again = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertNotEqual(ToolOutcomeKind.SUCCESS, again.kind)
        self.assertEqual(1, len(self.broker.sent))

    async def test_same_idempotency_key_with_different_payload_conflicts(self) -> None:
        first, first_raw = send_payload(body="one")
        self.artifacts.blobs[first["mime_artifact_id"]] = first_raw
        approval_one = await self._approve(first)
        success = await self.gateway.invoke(
            call(first, approval_id=approval_one, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.SUCCESS, success.kind)

        second, second_raw = send_payload(body="two")
        self.artifacts.blobs[second["mime_artifact_id"]] = second_raw
        approval_two = await self._approve(second)
        conflict = await self.gateway.invoke(
            call(second, approval_id=approval_two, idempotency_key=LOCAL_ACTION)
        )
        self.assertNotEqual(ToolOutcomeKind.SUCCESS, conflict.kind)
        self.assertEqual(1, len(self.broker.sent))

    async def test_idempotency_key_must_equal_the_local_action_id(self) -> None:
        arguments, raw = send_payload()
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key="other-action")
        )
        self.assertEqual(ToolOutcomeKind.FAILED, outcome.kind)
        self.assertEqual([], self.broker.sent)

    async def test_allowlist_denies_other_recipients_before_materialization(self) -> None:
        arguments, raw = send_payload()
        arguments["to"] = ["stranger@example.test"]
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.FAILED, outcome.kind)
        self.assertEqual(0, self.invoker.calls)
        self.assertEqual([], self.broker.sent)

    async def test_credential_failure_surfaces_as_user_action_and_records_failed(
        self,
    ) -> None:
        arguments, raw = send_payload()
        self.artifacts.blobs[arguments["mime_artifact_id"]] = raw
        approval_id = await self._approve(arguments)
        self.broker.raise_error = MailError(
            MailErrorCode.AUTH_FAILED, "credentials rejected"
        )
        outcome = await self.gateway.invoke(
            call(arguments, approval_id=approval_id, idempotency_key=LOCAL_ACTION)
        )
        self.assertEqual(ToolOutcomeKind.USER_ACTION_REQUIRED, outcome.kind)
        self.assertEqual([], self.broker.sent)
        self.assertEqual(MailDeliveryStatus.FAILED, self.ledger.records[LOCAL_ACTION].status)




class _BlockingInvoker(FakeInvoker):
    """Materialization blocks until the test releases it."""

    def __init__(self, artifacts: FakeArtifacts) -> None:
        super().__init__(artifacts)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke_tool(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.entered.set()
        await self.release.wait()
        return await super().invoke_tool(*args, **kwargs)


class AccountChangeRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_disabled_during_materialization_blocks_dispatch(self) -> None:
        from dataclasses import replace

        artifacts = FakeArtifacts()
        invoker = _BlockingInvoker(artifacts)
        broker = FakeBroker()
        ledger = FakeLedger()
        accounts = InMemoryMailAccountRegistry((ACCOUNT,))
        executor = MailSendExecutor(
            broker=broker,  # type: ignore[arg-type]
            artifacts=artifacts,
            invoker=invoker,
            ledger=ledger,  # type: ignore[arg-type]
            accounts=accounts,
            policy=MailPolicy(
                allow_send=True, allowed_recipients=frozenset(a.lower() for a in RECIPIENTS)
            ),
        )
        registry = ToolRegistry()
        registry.publish((descriptor(),))
        approvals = ApprovalService(InMemoryApprovalRepository())
        gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=approvals,
            executor=executor,
            outbox=InMemorySideEffectOutbox(approvals),
        )
        arguments, raw = send_payload()
        artifacts.blobs[arguments["mime_artifact_id"]] = raw
        needed = await gateway.invoke(
            call(arguments, approval_id=None, idempotency_key=LOCAL_ACTION)
        )
        approval = await approvals.get(needed.approval_id or "")
        await approvals.approve(approval.id, nonce=approval.nonce, actor_id="owner")

        task = asyncio.create_task(
            gateway.invoke(call(arguments, approval_id=approval.id, idempotency_key=LOCAL_ACTION))
        )
        await invoker.entered.wait()
        # The user disables sending while the worker is still materializing.
        await accounts.replace_all((replace(ACCOUNT, send_enabled=False),))
        invoker.release.set()
        outcome = await task
        self.assertEqual(ToolOutcomeKind.FAILED, outcome.kind, outcome.message)
        self.assertEqual([], broker.sent)
        self.assertEqual({}, ledger.records)

    async def test_credential_rebind_during_materialization_blocks_dispatch(self) -> None:
        from dataclasses import replace

        artifacts = FakeArtifacts()
        invoker = _BlockingInvoker(artifacts)
        broker = FakeBroker()
        ledger = FakeLedger()
        accounts = InMemoryMailAccountRegistry((ACCOUNT,))
        executor = MailSendExecutor(
            broker=broker,  # type: ignore[arg-type]
            artifacts=artifacts,
            invoker=invoker,
            ledger=ledger,  # type: ignore[arg-type]
            accounts=accounts,
            policy=MailPolicy(
                allow_send=True, allowed_recipients=frozenset(a.lower() for a in RECIPIENTS)
            ),
        )
        registry = ToolRegistry()
        registry.publish((descriptor(),))
        approvals = ApprovalService(InMemoryApprovalRepository())
        gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=approvals,
            executor=executor,
            outbox=InMemorySideEffectOutbox(approvals),
        )
        arguments, raw = send_payload()
        artifacts.blobs[arguments["mime_artifact_id"]] = raw
        needed = await gateway.invoke(
            call(arguments, approval_id=None, idempotency_key=LOCAL_ACTION)
        )
        approval = await approvals.get(needed.approval_id or "")
        await approvals.approve(approval.id, nonce=approval.nonce, actor_id="owner")

        task = asyncio.create_task(
            gateway.invoke(call(arguments, approval_id=approval.id, idempotency_key=LOCAL_ACTION))
        )
        await invoker.entered.wait()
        # The user re-binds the account to a different credential handle.
        await accounts.replace_all(
            (
                replace(
                    ACCOUNT,
                    secret_handle_id="different-handle",
                    smtp_host="attacker.example.test",
                ),
            )
        )
        invoker.release.set()
        outcome = await task
        self.assertEqual(ToolOutcomeKind.FAILED, outcome.kind, outcome.message)
        self.assertEqual([], broker.sent)
        self.assertEqual({}, ledger.records)


class _InFlightBroker(FakeBroker):
    """Keeps the transport call in flight while the test inspects wiring."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.guard: Any = None

    async def send(
        self, account_id: Any, request: Any, *, guard: Any = None
    ) -> MailDeliveryReceipt:
        self.guard = guard
        self.entered.set()
        await self.release.wait()
        return await super().send(account_id, request, guard=guard)


class OwnerWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_executor_uses_one_owner_for_claim_registry_and_heartbeat(self) -> None:
        from personal_assistant.infrastructure.mail.owners import (
            MailExecutionOwnerRegistry,
        )

        artifacts = FakeArtifacts()
        invoker = FakeInvoker(artifacts)
        broker = _InFlightBroker()
        ledger = FakeLedger()
        accounts = InMemoryMailAccountRegistry((ACCOUNT,))
        owners = MailExecutionOwnerRegistry()
        executor = MailSendExecutor(
            broker=broker,  # type: ignore[arg-type]
            artifacts=artifacts,
            invoker=invoker,
            ledger=ledger,  # type: ignore[arg-type]
            accounts=accounts,
            policy=MailPolicy(
                allow_send=True, allowed_recipients=frozenset(a.lower() for a in RECIPIENTS)
            ),
            owners=owners,
        )
        registry = ToolRegistry()
        registry.publish((descriptor(),))
        approvals = ApprovalService(InMemoryApprovalRepository())
        gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=approvals,
            executor=executor,
            outbox=InMemorySideEffectOutbox(approvals),
        )
        arguments, raw = send_payload()
        artifacts.blobs[arguments["mime_artifact_id"]] = raw
        needed = await gateway.invoke(
            call(arguments, approval_id=None, idempotency_key=LOCAL_ACTION)
        )
        approval = await approvals.get(needed.approval_id or "")
        await approvals.approve(approval.id, nonce=approval.nonce, actor_id="owner")

        task = asyncio.create_task(
            gateway.invoke(
                call(arguments, approval_id=approval.id, idempotency_key=LOCAL_ACTION)
            )
        )
        await broker.entered.wait()
        record = ledger.records[LOCAL_ACTION]
        # The ledger claim, the live-owner registry and the heartbeat all share
        # the exact same owner incarnation.
        self.assertIsNotNone(record.owner_id)
        self.assertIn(record.owner_id, owners.active())
        self.assertTrue(
            await ledger.heartbeat(
                LOCAL_ACTION, record.owner_id or "", lease_seconds=120
            )
        )
        broker.release.set()
        outcome = await task
        self.assertEqual(ToolOutcomeKind.SUCCESS, outcome.kind, outcome.message)
        self.assertEqual(frozenset(), owners.active())


class _FailingHeartbeatLedger(FakeLedger):
    """Heartbeat that fails the first ``failures`` attempts (or forever)."""

    def __init__(self, *, failures: int | None = None) -> None:
        super().__init__()
        self.failures = failures
        self.attempts = 0

    async def heartbeat(
        self, local_action_id: str, owner_id: str, *, lease_seconds: float
    ) -> bool:
        del local_action_id, owner_id, lease_seconds
        self.attempts += 1
        if self.failures is None or self.attempts <= self.failures:
            raise RuntimeError("ledger heartbeat unavailable")
        return True


class _HungHeartbeatLedger(FakeLedger):
    """Heartbeat that never returns, like a hung database connection."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()

    async def heartbeat(
        self, local_action_id: str, owner_id: str, *, lease_seconds: float
    ) -> bool:
        del local_action_id, owner_id, lease_seconds
        self.entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _SlowReconfirmRegistry(InMemoryMailAccountRegistry):
    """Delays the post-claim account reconfirmation between begin and guard."""

    def __init__(
        self, records: tuple[MailAccountRecord, ...], *, delay: float
    ) -> None:
        super().__init__(records)
        self._delay = delay
        self.resolves = 0

    async def resolve(self, account_id: str) -> MailAccountRecord:
        self.resolves += 1
        if self.resolves >= 3:
            await asyncio.sleep(self._delay)
        return await super().resolve(account_id)


async def _wait_until(predicate: Any, *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the condition was not reached before the deadline")


class HeartbeatDeadlineTests(unittest.IsolatedAsyncioTestCase):
    """The local lease deadline is authoritative, not an unbounded retry loop."""

    def _executor(
        self, ledger: FakeLedger, *, lease: float, interval: float
    ) -> MailSendExecutor:
        artifacts = FakeArtifacts()
        return MailSendExecutor(
            broker=FakeBroker(),  # type: ignore[arg-type]
            artifacts=artifacts,
            invoker=FakeInvoker(artifacts),  # type: ignore[arg-type]
            ledger=ledger,  # type: ignore[arg-type]
            accounts=InMemoryMailAccountRegistry((ACCOUNT,)),
            policy=MailPolicy(
                allow_send=True,
                allowed_recipients=frozenset(a.lower() for a in RECIPIENTS),
            ),
            lease_seconds=lease,
            heartbeat_interval_seconds=interval,
        )

    async def test_continuous_heartbeat_failures_expire_the_local_lease(self) -> None:
        from personal_assistant.infrastructure.mail.executor import _LeaseGuard

        ledger = _FailingHeartbeatLedger()
        executor = self._executor(ledger, lease=0.4, interval=0.05)
        guard = _LeaseGuard(0.4)
        task = asyncio.create_task(
            executor._heartbeat_loop(LOCAL_ACTION, "owner-1", guard)
        )
        try:
            await _wait_until(lambda: not guard.valid)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # The deadline expired and the guard is invalid: the transport must
        # abort instead of racing a reconciliation with a stale lease.
        self.assertFalse(guard.valid)
        self.assertEqual("EXECUTION_LEASE_LOST", guard.reason)
        self.assertGreaterEqual(ledger.attempts, 2)

    async def test_transient_heartbeat_failures_keep_the_lease_until_renewal(
        self,
    ) -> None:
        from personal_assistant.infrastructure.mail.executor import _LeaseGuard

        ledger = _FailingHeartbeatLedger(failures=3)
        executor = self._executor(ledger, lease=1.0, interval=0.05)
        guard = _LeaseGuard(1.0)
        task = asyncio.create_task(
            executor._heartbeat_loop(LOCAL_ACTION, "owner-1", guard)
        )
        try:
            await _wait_until(lambda: ledger.attempts >= 4 and guard.valid)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # Failures before the deadline are tolerated; the lease stays valid.
        self.assertTrue(guard.valid)
        self.assertIsNone(guard.reason)

    async def test_hung_heartbeat_cannot_outlive_the_local_lease(self) -> None:
        from personal_assistant.infrastructure.mail.executor import _LeaseGuard

        ledger = _HungHeartbeatLedger()
        executor = self._executor(ledger, lease=0.4, interval=0.05)
        guard = _LeaseGuard(0.4)
        task = asyncio.create_task(
            executor._heartbeat_loop(LOCAL_ACTION, "owner-1", guard)
        )
        await _wait_until(lambda: not guard.valid, timeout=2.0)
        # The loop itself must also end: the heartbeat call is bounded by the
        # remaining lease, so nothing stays suspended past the deadline.
        await asyncio.wait_for(task, timeout=2.0)
        self.assertFalse(guard.valid)
        self.assertEqual("EXECUTION_LEASE_LOST", guard.reason)

    async def test_heartbeat_interval_larger_than_lease_fails_closed(self) -> None:
        from personal_assistant.infrastructure.mail.executor import _LeaseGuard

        ledger = _FailingHeartbeatLedger(failures=0)
        executor = self._executor(ledger, lease=0.3, interval=5.0)
        guard = _LeaseGuard(0.3)
        task = asyncio.create_task(
            executor._heartbeat_loop(LOCAL_ACTION, "owner-1", guard)
        )
        try:
            await asyncio.sleep(0.6)
            # The interval exceeds the lease: the guard must already have
            # expired even though the loop is still sleeping.
            self.assertFalse(guard.valid)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_guard_deadline_is_anchored_to_the_claim_origin(self) -> None:
        from personal_assistant.infrastructure.mail.executor import _LeaseGuard

        # Created long after the claim was issued: the guard must inherit the
        # claim origin instead of restarting a full lease.
        guard = _LeaseGuard(0.4, started_at=time.monotonic() - 0.5)
        self.assertFalse(guard.valid)
        self.assertEqual("EXECUTION_LEASE_LOST", guard.reason)
        guard.renew(0.4)
        self.assertFalse(guard.valid)

    async def test_post_claim_delay_never_dispatches_with_an_expired_lease(
        self,
    ) -> None:
        artifacts = FakeArtifacts()
        invoker = FakeInvoker(artifacts)
        broker = FakeBroker()
        ledger = FakeLedger()
        accounts = _SlowReconfirmRegistry((ACCOUNT,), delay=0.6)
        executor = MailSendExecutor(
            broker=broker,  # type: ignore[arg-type]
            artifacts=artifacts,
            invoker=invoker,  # type: ignore[arg-type]
            ledger=ledger,  # type: ignore[arg-type]
            accounts=accounts,
            policy=MailPolicy(
                allow_send=True,
                allowed_recipients=frozenset(a.lower() for a in RECIPIENTS),
            ),
            lease_seconds=0.4,
            heartbeat_interval_seconds=0.05,
        )
        registry = ToolRegistry()
        registry.publish((descriptor(),))
        approvals = ApprovalService(InMemoryApprovalRepository())
        gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=approvals,
            executor=executor,
            outbox=InMemorySideEffectOutbox(approvals),
        )
        arguments, raw = send_payload()
        artifacts.blobs[arguments["mime_artifact_id"]] = raw
        needed = await gateway.invoke(
            call(arguments, approval_id=None, idempotency_key=LOCAL_ACTION)
        )
        approval = await approvals.get(needed.approval_id or "")
        await approvals.approve(approval.id, nonce=approval.nonce, actor_id="owner")

        outcome = await gateway.invoke(
            call(arguments, approval_id=approval.id, idempotency_key=LOCAL_ACTION)
        )
        # The post-claim reconfirmation outlived the ledger lease: no SMTP call
        # happens and the durable EXECUTING row is left for recovery.
        self.assertEqual(ToolOutcomeKind.OUTCOME_UNKNOWN, outcome.kind, outcome.message)
        self.assertEqual([], broker.sent)
        self.assertEqual(
            MailDeliveryStatus.EXECUTING, ledger.records[LOCAL_ACTION].status
        )


class CompositeGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_lease_lost_while_waiting_for_the_account_lock_blocks_data(
        self,
    ) -> None:
        from personal_assistant.infrastructure.mail.executor import _LeaseGuard
        from personal_assistant.infrastructure.mail.owners import CompositeMailGuard

        registry = InMemoryMailAccountRegistry((ACCOUNT,))
        record = await registry.resolve(ACCOUNT.account_id)
        account_guard = registry.dispatch_guard(record)
        self.assertTrue(account_guard.begin_critical_section())
        lease_guard = _LeaseGuard(0.2)
        composite = CompositeMailGuard(lease_guard, account_guard)
        results: list[bool] = []
        thread = threading.Thread(
            target=lambda: results.append(composite.begin_critical_section(2.0))
        )
        thread.start()
        try:
            # The composite blocks on the account lock while the lease expires.
            await asyncio.sleep(0.3)
            self.assertFalse(lease_guard.valid)
        finally:
            account_guard.end_critical_section()
        await asyncio.to_thread(thread.join, 5.0)
        # All guards are re-verified after every lock is held: the expired
        # lease must fail the composite begin and release the account lock.
        self.assertEqual([False], results)
        self.assertTrue(account_guard.begin_critical_section(0.5))
        account_guard.end_critical_section()


if __name__ == "__main__":

    unittest.main()
