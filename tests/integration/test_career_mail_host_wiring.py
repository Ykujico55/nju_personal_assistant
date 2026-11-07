"""Career SDK materialization through real host review, approval and mail executor.

Only the transport and artifact byte store are synthetic. No network, original
runtime directory, personal account or production database is accessed.
"""

from __future__ import annotations

import base64
import hashlib
import sys
import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from personal_assistant_sdk import (
    ArtifactHandle,
    InvocationContext,
    MailAccountInfo,
    RuntimeContext,
)
from personal_assistant_sdk.models import to_jsonable

from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.mail import (
    MailAccountRecord,
    MailDeliveryReceipt,
    MailDeliveryStatus,
    MailPolicy,
    MailRecipientResult,
    MailRecipientStatus,
)
from personal_assistant.core.tools import ToolGateway, ToolPolicy, ToolRegistry
from personal_assistant.core.tools.gateway import DefinitiveToolFailure
from personal_assistant.domain import ApprovalState, RiskLevel, ToolDescriptor, ToolOutcomeKind
from personal_assistant.infrastructure.mail.executor import MailSendExecutor
from personal_assistant.infrastructure.mail.registry import InMemoryMailAccountRegistry
from personal_assistant.infrastructure.memory import InMemorySideEffectOutbox
from personal_assistant.infrastructure.memory.mail import InMemoryMailDeliveryLedger
from personal_assistant.infrastructure.tools.capability_router import CapabilityRoutingExecutor

PLUGIN_SRC = Path(__file__).resolve().parents[2] / "extensions" / "career_apply" / "src"
if str(PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SRC))

from career_apply.worker import TOOL_SCHEMAS, CareerApplyExtension  # noqa: E402

RESUME_BYTES = b"%PDF-1.4\npublic synthetic career fixture\n%%EOF"
RECIPIENT = "career-recipient@example.test"


class SharedArtifacts:
    def __init__(self):
        self.blobs = {}

    async def put(self, data, *, media_type, sensitivity="PERSONAL"):
        digest = hashlib.sha256(data).hexdigest()
        self.blobs[digest] = data
        return ArtifactHandle(digest, digest, media_type, len(data))

    async def read(self, identifier):
        return self.blobs[identifier]

    async def read_as_host(self, identifier):
        return await self.read(identifier)

    async def delete(self, identifier):
        self.blobs.pop(identifier, None)

    async def aclose(self):
        return None


class SDKMailAccount:
    def __init__(self, accounts):
        self.accounts = accounts

    async def account(self, identifier):
        account = await self.accounts.resolve(identifier)
        return MailAccountInfo(
            account.account_id,
            account.address,
            account.display_name,
            account.read_enabled,
            account.send_enabled,
            account.fingerprint(),
        )


class SyntheticSMTPBroker:
    def __init__(self):
        self.sent = []

    @property
    def send_available(self):
        return True

    async def send(self, account_id, request, *, guard=None):
        self.sent.append(request)
        return MailDeliveryReceipt(
            local_action_id=request.local_action_id,
            message_id=request.message_id,
            status=MailDeliveryStatus.SUCCEEDED,
            recipient_results=tuple(
                MailRecipientResult(address, MailRecipientStatus.ACCEPTED)
                for address in request.recipients()
            ),
            server_code="250_SYNTHETIC",
        )


class ActualWorkerInvoker:
    def __init__(self, worker):
        self.worker = worker

    async def invoke_tool(
        self,
        extension_id,
        tool_id,
        arguments: Mapping[str, Any],
        *,
        task_id,
        run_id,
        idempotency_key,
        deadline_seconds,
    ):
        if extension_id != "career.apply":
            raise AssertionError("wrong extension")
        response = await self.worker.invoke(
            tool_id,
            dict(arguments),
            InvocationContext(
                task_id,
                run_id,
                "2030-01-01T00:00:00Z",
                idempotency_key,
            ),
        )
        return to_jsonable(response)


def descriptor(tool_id, risk, capabilities=()):
    schemas = TOOL_SCHEMAS[tool_id]
    return ToolDescriptor(
        tool_id,
        "0.1.0",
        "career.apply",
        schemas[0],
        schemas[1],
        risk,
        extension_version="0.1.0",
        required_capabilities=frozenset(capabilities),
    )


class CareerMailHostWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.artifacts = SharedArtifacts()
        self.accounts = InMemoryMailAccountRegistry(
            (
                MailAccountRecord(
                    account_id="career_fixture",
                    address="career-sender@example.test",
                    imap_host="imap.example.test",
                    smtp_host="smtp.example.test",
                    secret_handle_id="synthetic-unused-handle",
                    send_enabled=True,
                ),
            )
        )
        self.worker = CareerApplyExtension()
        await self.worker.initialize(
            RuntimeContext(
                "1",
                "career.apply",
                "0.1.0",
                "ext_career_fixture",
                non_secret_config={"account_id": "career_fixture"},
                host_mail=SDKMailAccount(self.accounts),
                host_artifact=self.artifacts,
            )
        )
        self.addAsyncCleanup(self.worker.shutdown)
        context = InvocationContext("career_task", "prepare", "2030-01-01T00:00:00Z", "prepare1")
        imported = await self.worker.invoke(
            "career.resume.import",
            {
                "filename": "resume.pdf",
                "media_type": "application/pdf",
                "data_base64": base64.b64encode(RESUME_BYTES).decode(),
            },
            context,
        )
        self.assertEqual("SUCCEEDED", imported.outcome.value)
        prepared = await self.worker.invoke(
            "career.mail.prepare",
            {
                "to": RECIPIENT,
                "subject": "Synthetic career application",
                "body": "This is public synthetic material; no actual delivery.",
                "prepared_at": "2026-10-30T01:00:00+00:00",
                "resume": dict(imported.output),
            },
            context,
        )
        self.assertEqual("SUCCEEDED", prepared.outcome.value)
        self.arguments = dict(prepared.output["send_arguments"])
        self.send = descriptor("career.mail.send", RiskLevel.EXTERNAL_WRITE, ("mail.send",))
        self.preview = descriptor("career.mail.send.preview", RiskLevel.READ)
        self.broker = SyntheticSMTPBroker()
        self.ledger = InMemoryMailDeliveryLedger()
        self.addAsyncCleanup(self.ledger.close)
        self.invoker = ActualWorkerInvoker(self.worker)
        self.executor = MailSendExecutor(
            broker=self.broker,
            artifacts=self.artifacts,
            invoker=self.invoker,
            ledger=self.ledger,
            accounts=self.accounts,
            policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({RECIPIENT})),
        )
        registry = ToolRegistry()
        registry.publish((self.send, self.preview))
        self.approvals = ApprovalService(InMemoryApprovalRepository())
        self.gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=self.approvals,
            executor=CapabilityRoutingExecutor(
                extension_router=self.invoker, mail_send=self.executor
            ),
            outbox=InMemorySideEffectOutbox(self.approvals),
        )

    async def prepared_call(self, arguments=None):
        return await self.executor.prepare_call(
            self.send,
            self.preview,
            arguments or self.arguments,
            task_id="career_task",
            granted_capabilities=frozenset({"mail.send"}),
            workflow_allowed_tools=frozenset({"career.mail.send"}),
        )

    async def approve(self, call):
        required = await self.gateway.invoke(call)
        self.assertEqual(ToolOutcomeKind.APPROVAL_REQUIRED, required.kind)
        record = await self.approvals.get(required.approval_id)
        await self.approvals.approve(record.id, nonce=record.nonce, actor_id="fixture-owner")
        return replace(call, approval_id=record.id)

    async def test_actual_plugin_mime_becomes_host_verified_approval_material(self):
        call = await self.prepared_call()
        review = await self.executor.review(
            self.send, self.preview, self.arguments, task_id="career_task"
        )
        self.assertEqual({"account_id": "career_fixture"}, call.target)
        self.assertEqual(self.arguments["local_action_id"], call.idempotency_key)
        self.assertEqual(1, len(call.attachments))
        self.assertEqual("resume.pdf", call.attachments[0].name)
        self.assertEqual(hashlib.sha256(RESUME_BYTES).hexdigest(), call.attachments[0].sha256)
        self.assertEqual(len(RESUME_BYTES), call.attachments[0].size_bytes)
        self.assertEqual([RECIPIENT], review["to"])
        self.assertIn("public synthetic", review["body_text"])
        self.assertEqual([], self.broker.sent)
        self.assertIsNone(await self.ledger.get(self.arguments["local_action_id"]))

    async def test_approval_dispatches_exact_bytes_once_through_real_host_executor(self):
        call = await self.prepared_call()
        approved = await self.approve(call)
        self.assertEqual([], self.broker.sent)
        sent = await self.gateway.invoke(approved)
        self.assertEqual(ToolOutcomeKind.SUCCESS, sent.kind, sent.message)
        self.assertEqual(1, len(self.broker.sent))
        request = self.broker.sent[0]
        self.assertEqual((RECIPIENT,), request.to)
        self.assertEqual(
            self.artifacts.blobs[self.arguments["mime_artifact_id"]], request.mime_bytes
        )
        self.assertEqual(
            hashlib.sha256(request.mime_bytes).hexdigest(), self.arguments["mime_sha256"]
        )
        repeated = await self.gateway.invoke(approved)
        self.assertIn(repeated.kind, (ToolOutcomeKind.SUCCESS, ToolOutcomeKind.FAILED))
        self.assertEqual(1, len(self.broker.sent))
        self.assertEqual(
            ApprovalState.SUCCEEDED, (await self.approvals.get(approved.approval_id)).state
        )
        self.assertEqual(
            MailDeliveryStatus.SUCCEEDED,
            (await self.ledger.get(self.arguments["local_action_id"])).status,
        )

    async def test_bad_attachment_manifest_is_rejected_before_transport(self):
        with self.assertRaises(DefinitiveToolFailure):
            await self.prepared_call({**self.arguments, "attachment_hashes": ["f" * 64]})
        self.assertEqual([], self.broker.sent)
        self.assertIsNone(await self.ledger.get(self.arguments["local_action_id"]))

    async def test_artifact_tampering_after_approval_never_dispatches(self):
        approved = await self.approve(await self.prepared_call())
        self.artifacts.blobs[self.arguments["mime_artifact_id"]] += b"tampered"
        rejected = await self.gateway.invoke(approved)
        self.assertEqual(ToolOutcomeKind.FAILED, rejected.kind)
        self.assertEqual([], self.broker.sent)
        self.assertNotEqual(
            ApprovalState.SUCCEEDED, (await self.approvals.get(approved.approval_id)).state
        )
