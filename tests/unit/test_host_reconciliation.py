from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from datetime import timedelta

from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.core.approvals.service import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.browser.models import BrowserSessionState
from personal_assistant.core.browser.ports import BrowserSessionRecord
from personal_assistant.core.jobs.outbox import SideEffectState
from personal_assistant.core.mail import MailDeliveryRecord, MailDeliveryStatus
from personal_assistant.core.tools import ToolGateway
from personal_assistant.domain import ApprovalState, RiskLevel, ToolCall, ToolDescriptor
from personal_assistant.domain.models import utc_now
from personal_assistant.infrastructure.memory.browser import InMemoryBrowserSessionStore
from personal_assistant.infrastructure.memory.mail import InMemoryMailDeliveryLedger
from personal_assistant.infrastructure.memory.outbox import InMemorySideEffectOutbox
from personal_assistant.infrastructure.operations.reconciliation import HostReconciliationResolver


class HostReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.approvals = ApprovalService(InMemoryApprovalRepository())
        self.outbox = InMemorySideEffectOutbox(self.approvals)
        self.mail = InMemoryMailDeliveryLedger()
        self.browser = InMemoryBrowserSessionStore()
        self.resolver = HostReconciliationResolver(
            approvals=self.approvals, outbox=self.outbox,
            mail_ledger=self.mail, browser_sessions=self.browser,
        )

    async def unknown(self, arguments, capability, reference):
        descriptor = ToolDescriptor(
            id="fixture.external", version="1", extension_id="fixture",
            extension_version="1", risk=RiskLevel.EXTERNAL_WRITE,
            input_schema={"type": "object"}, output_schema={"type": "object"},
            required_capabilities=(capability,),
        )
        call = ToolCall(
            tool_id=descriptor.id, tool_version="1", task_id="task-fixture",
            arguments=arguments, target={"synthetic": "only"},
            idempotency_key=arguments.get("local_action_id", "browser-key"),
            granted_capabilities=frozenset({capability}),
            workflow_allowed_tools=frozenset({descriptor.id}),
        )
        binding = ToolGateway._approval_binding(descriptor, call)
        approval = await self.approvals.prepare(binding)
        await self.approvals.approve(approval.id, nonce=approval.nonce, actor_id="fixture")
        call = replace(call, approval_id=approval.id)
        intent = ToolGateway._side_effect_intent(descriptor, call, binding)
        await self.outbox.create_with_approval_consumption(intent)
        await self.outbox.finalize(
            intent.id, approval.id, state=SideEffectState.UNKNOWN, result_reference=reference,
        )
        self.call, self.descriptor, self.intent = call, descriptor, intent
        return call

    async def resolve(self, call=None, **changes):
        fields = dict(
            extension_id="fixture", extension_version="1",
            required_capabilities=frozenset(self.descriptor.required_capabilities),
        )
        fields.update(changes)
        return await self.resolver.resolve(call or self.call, **fields)

    async def mail_fixture(self, *, reconciled=True):
        args = {
            "local_action_id": "action-fixture", "account_id": "fixture-account",
            "message_id": "<fixture@example.invalid>", "mime_sha256": "a" * 64,
        }
        await self.unknown(args, "mail.send", args["local_action_id"])
        record = MailDeliveryRecord(
            local_action_id=args["local_action_id"], account_id=args["account_id"],
            message_id=args["message_id"], status=MailDeliveryStatus.PREPARED,
            envelope_digest=canonical_sha256(args), mime_sha256=args["mime_sha256"],
        )
        await self.mail.prepare(record)
        await self.mail.begin_execution(
            record.local_action_id, record.envelope_digest, owner_id="fixture", lease_seconds=30,
        )
        await self.mail.finalize(
            record.local_action_id, record.envelope_digest, status=MailDeliveryStatus.UNKNOWN,
        )
        if reconciled:
            await self.mail.reconcile(
                record.local_action_id, account_id=record.account_id,
                message_id=record.message_id, status=MailDeliveryStatus.SUCCEEDED,
                diagnostic_code="SENT_RECONCILED",
            )
        return record

    async def test_mail_reconciliation_is_read_only_and_keeps_consumed_authorization(self):
        await self.mail_fixture()
        fact = await self.resolve()
        self.assertIsNotNone(fact)
        self.assertEqual("SUCCEEDED", fact.result["status"])
        self.assertEqual("action-fixture", fact.result["local_action_id"])
        approval = await self.approvals.get(self.call.approval_id)
        self.assertEqual(ApprovalState.UNKNOWN, approval.state)
        self.assertIsNotNone(approval.consumed_at)
        intent = await self.outbox.get_by_call(self.call.tool_id, self.call.idempotency_key)
        self.assertEqual(SideEffectState.UNKNOWN, intent.state)
        self.assertEqual(fact, await self.resolve())

    async def test_unresolved_or_unbound_mail_never_mints_evidence(self):
        await self.mail_fixture(reconciled=False)
        self.assertIsNone(await self.resolve())

        # Missing authoritative data also remains a refusal.
        # No second action is created: missing authoritative data remains a refusal.
        self.mail._records.clear()
        self.assertIsNone(await self.resolve())

    async def test_call_and_descriptor_drift_are_rejected(self):
        await self.mail_fixture()
        for changes in (
            {"task_id": "other"}, {"tool_version": "2"}, {"target": {"other": True}},
            {"idempotency_key": "other"}, {"arguments": {**self.call.arguments, "extra": 1}},
            {"approval_id": "missing"},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(await self.resolve(replace(self.call, **changes)))
        self.assertIsNone(await self.resolve(extension_id="other"))
        self.assertIsNone(await self.resolve(extension_version="2"))
        self.assertIsNone(await self.resolve(required_capabilities=frozenset()))

    async def test_outbox_binding_and_ledger_binding_drift_are_rejected(self):
        await self.mail_fixture()
        original = self.outbox._intents[self.intent.id]
        for field, value in (
            ("task_id", "other"), ("approval_id", "other"),
            ("canonical_payload_sha256", "b" * 64), ("action_fingerprint", "b" * 64),
            ("state", SideEffectState.FAILED),
        ):
            with self.subTest(field=field):
                self.outbox._intents[self.intent.id] = replace(original, **{field: value})
                self.assertIsNone(await self.resolve())
        self.outbox._intents[self.intent.id] = original
        original_mail = self.mail._records["action-fixture"]
        for field, value in (
            ("account_id", "other"), ("message_id", "other"), ("mime_sha256", "b" * 64),
            ("envelope_digest", "b" * 64), ("diagnostic_code", "unverified"),
            ("status", MailDeliveryStatus.PARTIAL),
        ):
            with self.subTest(field=field):
                self.mail._records["action-fixture"] = replace(original_mail, **{field: value})
                self.assertIsNone(await self.resolve())

    async def browser_fixture(self):
        args = {
            "session_id": "session-fixture", "preview_hash": "b" * 64,
            "preview_nonce": "fixture-nonce", "action_id": "submit",
        }
        await self.unknown(args, "browser.submit", "browser-submit:session-fixture")
        now = utc_now()
        receipt = {
            "issued_by": "host_tracking", "session_id": args["session_id"],
            "transaction_id": "transaction", "adapter_id": "adapter", "adapter_version": "1",
            "reference": "synthetic-receipt", "observed_at": now.isoformat(),
            "reference_sha256": hashlib.sha256(b"synthetic-receipt").hexdigest(),
        }
        record = BrowserSessionRecord(
            session_id=args["session_id"], task_id=self.call.task_id, extension_id="fixture",
            extension_version="1", purpose="synthetic", state=BrowserSessionState.SUCCEEDED,
            created_at=now, updated_at=now, expires_at=now + timedelta(minutes=5),
            preview_hash=args["preview_hash"], preview_nonce=args["preview_nonce"],
            preview={
                "target_action_id": "submit", "session_id": args["session_id"],
                "canonical_payload_hash": args["preview_hash"], "nonce": args["preview_nonce"],
                "extension_id": "fixture", "extension_version": "1",
                "transaction_id": "transaction", "adapter_id": "adapter", "adapter_version": "1",
            }, outcome="SUCCEEDED", receipt=receipt,
            transaction_id="transaction", adapter_id="adapter", adapter_version="1",
            diagnostic_code="RECONCILED", submit_count=1,
        )
        await self.browser.create(record)
        return record

    async def test_browser_uses_only_exact_host_tracking_receipt(self):
        await self.browser_fixture()
        fact = await self.resolve()
        self.assertIsNotNone(fact)
        self.assertEqual("SUCCEEDED", fact.result["state"])
        self.assertEqual("synthetic-receipt", fact.result["reference"])

    async def test_browser_foreign_or_unverified_proof_is_rejected(self):
        record = await self.browser_fixture()
        for field, value in (
            ("task_id", "other"), ("extension_id", "other"), ("extension_version", "2"),
            ("preview_hash", "c" * 64), ("preview_nonce", "other"),
            ("state", BrowserSessionState.UNKNOWN), ("submit_count", 0),
        ):
            with self.subTest(field=field):
                self.browser._records[record.session_id] = replace(record, **{field: value})
                self.assertIsNone(await self.resolve())
        for field, value in (
            ("issued_by", "caller"), ("session_id", "other"), ("adapter_version", "2"),
            ("transaction_id", "other"), ("reference_sha256", "c" * 64),
        ):
            with self.subTest(receipt=field):
                self.browser._records[record.session_id] = replace(
                    record, receipt={**record.receipt, field: value},
                )
                self.assertIsNone(await self.resolve())

    async def test_generic_gateway_unknown_reference_does_not_erase_host_evidence(self):
        await self.mail_fixture()
        approval = await self.approvals.get(self.call.approval_id)
        await self.approvals._repository.save(
            replace(approval, version=approval.version + 1,
                    result_reference="unknown:synthetic-timeout"),
            expected_version=approval.version,
        )
        self.assertIsNotNone(await self.resolve())

    async def test_returned_proof_and_outbox_read_are_detached(self):
        await self.mail_fixture()
        intent = await self.outbox.get_by_call(self.call.tool_id, self.call.idempotency_key)
        intent.receipt["tampered"] = True
        fact = await self.resolve()
        fact.receipt["action_fingerprint"] = "tampered"
        fact.result["status"] = "FAILED"
        reread = await self.outbox.get_by_call(self.call.tool_id, self.call.idempotency_key)
        self.assertNotIn("tampered", reread.receipt)
        self.assertEqual("SUCCEEDED", (await self.resolve()).result["status"])


    async def test_approval_must_be_consumed_and_exactly_bound(self):
        await self.mail_fixture()
        original = await self.approvals.get(self.call.approval_id)
        for changes in (
            {"consumed_at": None}, {"action_fingerprint": "c" * 64},
            {"canonical_action": "{}"}, {"state": ApprovalState.FAILED},
            {"state": ApprovalState.APPROVED},
        ):
            with self.subTest(changes=changes):
                self.approvals._repository._records[original.id] = replace(original, **changes)
                self.assertIsNone(await self.resolve())
        self.approvals._repository._records[original.id] = original
        self.assertIsNone(await self.resolve(replace(self.call, granted_capabilities=frozenset())))
        self.assertIsNone(await self.resolve(
            replace(self.call, workflow_allowed_tools=frozenset())
        ))
        self.assertIsNone(await self.resolve(
            required_capabilities=frozenset({"mail.send", "browser.submit"}),
        ))

    async def test_browser_preview_and_success_are_required(self):
        record = await self.browser_fixture()
        for field in ("session_id", "nonce", "canonical_payload_hash", "extension_version",
                      "transaction_id", "adapter_version", "target_action_id"):
            with self.subTest(field=field):
                self.browser._records[record.session_id] = replace(
                    record, preview={**record.preview, field: "other"},
                )
                self.assertIsNone(await self.resolve())
        self.browser._records[record.session_id] = replace(record, receipt=None)
        self.assertIsNone(await self.resolve())
        self.browser._records[record.session_id] = replace(record, diagnostic_code="unverified")
        self.assertIsNone(await self.resolve())

    async def test_real_browser_broker_issues_then_resolver_consumes_bound_proof(self):
        from personal_assistant.domain import ToolOutcomeKind
        from tests.unit import test_browser_executor_f07 as browser_fixture

        fixture = browser_fixture.SubmitGatewayTests(methodName="runTest")
        session_id, arguments, _ = await fixture.reach_preview()
        self.addAsyncCleanup(fixture.broker.aclose)
        fixture.companion.click_outcome = "UNKNOWN"
        call = fixture.call("ehall.submit", arguments, idempotency_key="fixture-submit-once")
        pending = await fixture.gateway.invoke(call)
        approval = await fixture.approvals.get(pending.approval_id)
        await fixture.approvals.approve(approval.id, nonce=approval.nonce, actor_id="fixture")
        approved_call = replace(call, approval_id=approval.id)
        outcome = await fixture.gateway.invoke(approved_call)
        self.assertEqual(ToolOutcomeKind.OUTCOME_UNKNOWN, outcome.kind)
        resolver = HostReconciliationResolver(
            approvals=fixture.approvals, outbox=fixture.outbox, browser_sessions=fixture.sessions,
        )
        binding = dict(
            extension_id=browser_fixture.EXTENSION_ID,
            extension_version=browser_fixture.EXTENSION_VERSION,
            required_capabilities=frozenset({"browser.submit"}),
        )
        self.assertIsNone(await resolver.resolve(approved_call, **binding))
        fixture.companion.tracking_matches = ["NJU-2026-0101"]
        reconciled = await fixture.broker.reconcile(
            session_id, extension_id=browser_fixture.EXTENSION_ID,
        )
        self.assertEqual(BrowserSessionState.SUCCEEDED, reconciled.state)
        fact = await resolver.resolve(approved_call, **binding)
        self.assertIsNotNone(fact)
        self.assertEqual("NJU-2026-0101", fact.result["reference"])
        self.assertEqual(1, len(fixture.companion.click_calls))
        self.assertEqual(ApprovalState.UNKNOWN, (await fixture.approvals.get(approval.id)).state)
        self.assertEqual(fact, await resolver.resolve(approved_call, **binding))
        self.assertEqual(1, len(fixture.companion.click_calls))
    async def completed_mail_gateway(self, *, lose_gateway_finalize=False):
        from unittest.mock import patch

        from personal_assistant.domain import ToolOutcomeKind
        from tests.unit import test_mail_send_gateway_f06 as mail_fixture

        fixture = mail_fixture.MailSendGatewayTests(methodName="runTest")
        await fixture.asyncSetUp()
        fixture.ledger = InMemoryMailDeliveryLedger()
        fixture.executor._ledger = fixture.ledger
        args, raw = mail_fixture.send_payload()
        fixture.artifacts.blobs[args["mime_artifact_id"]] = raw
        approval_id = await fixture._approve(args)
        call = mail_fixture.call(
            args, approval_id=approval_id, idempotency_key=mail_fixture.LOCAL_ACTION,
        )
        original_finalize = fixture.gateway.outbox.finalize

        async def finalize(*positional, **kwargs):
            if lose_gateway_finalize and kwargs["state"] is SideEffectState.SUCCEEDED:
                raise RuntimeError("synthetic lost Gateway completion")
            return await original_finalize(*positional, **kwargs)

        with patch.object(fixture.gateway.outbox, "finalize", side_effect=finalize):
            if lose_gateway_finalize:
                with self.assertRaisesRegex(RuntimeError, "synthetic lost Gateway completion"):
                    await fixture.gateway.invoke(call)
            else:
                outcome = await fixture.gateway.invoke(call)
                self.assertEqual(ToolOutcomeKind.SUCCESS, outcome.kind)
        self.assertEqual(1, len(fixture.broker.sent))
        record = await fixture.ledger.get(args["local_action_id"])
        self.assertEqual(MailDeliveryStatus.SUCCEEDED, record.status)
        self.assertIsNone(record.diagnostic_code)
        resolver = HostReconciliationResolver(
            approvals=fixture.approvals, outbox=fixture.gateway.outbox, mail_ledger=fixture.ledger,
        )
        descriptor = mail_fixture.descriptor()
        binding = dict(
            extension_id=descriptor.extension_id, extension_version=descriptor.extension_version,
            required_capabilities=frozenset(descriptor.required_capabilities),
        )
        return fixture, call, resolver, binding

    async def test_real_mail_executor_success_can_resolve_lost_runtime_receipt(self):
        fixture, call, resolver, binding = await self.completed_mail_gateway()
        fact = await resolver.resolve(call, **binding)
        self.assertIsNotNone(fact)
        self.assertEqual("SUCCEEDED", fact.result["status"])
        self.assertEqual(1, len(fixture.broker.sent))
        self.assertEqual(
            ApprovalState.SUCCEEDED, (await fixture.approvals.get(call.approval_id)).state,
        )

    async def test_real_mail_executor_success_survives_gateway_completion_failure(self):
        fixture, call, resolver, binding = await self.completed_mail_gateway(
            lose_gateway_finalize=True,
        )
        fact = await resolver.resolve(call, **binding)
        self.assertIsNotNone(fact)
        self.assertEqual("SUCCEEDED", fact.result["status"])
        self.assertEqual(
            ApprovalState.EXECUTING, (await fixture.approvals.get(call.approval_id)).state,
        )
        self.assertEqual(1, len(fixture.broker.sent))
        # The normal startup recovery may later mark the interrupted pair UNKNOWN.
        intent = await fixture.gateway.outbox.get_by_call(call.tool_id, call.idempotency_key)
        await fixture.gateway.outbox.finalize(
            intent.id, call.approval_id, state=SideEffectState.UNKNOWN,
            result_reference="unknown:synthetic-interrupted-finalize",
        )
        self.assertIsNotNone(await resolver.resolve(call, **binding))
        self.assertEqual(1, len(fixture.broker.sent))

    async def test_smtp_success_requires_complete_recipient_acceptance_and_server_receipt(self):
        from personal_assistant.core.mail import MailRecipientStatus

        fixture, call, resolver, binding = await self.completed_mail_gateway()
        record = await fixture.ledger.get(call.idempotency_key)
        for changes in (
            {"server_code": None}, {"server_code": "550"}, {"recipient_results": ()},
            {"recipient_results": record.recipient_results[:-1]},
            {"recipient_results": (replace(record.recipient_results[0],
                                           status=MailRecipientStatus.UNKNOWN),
                                   *record.recipient_results[1:])},
            {"status": MailDeliveryStatus.PARTIAL},
        ):
            with self.subTest(changes=changes):
                fixture.ledger._records[record.local_action_id] = replace(record, **changes)
                self.assertIsNone(await resolver.resolve(call, **binding))