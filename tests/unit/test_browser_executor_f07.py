"""F07 Gate 4/5: browser actions behind the real Tool Gateway and R2 approvals."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.browser import BrowserSessionBroker
from personal_assistant.core.tools import ToolGateway, ToolPolicy, ToolRegistry
from personal_assistant.domain import RiskLevel, ToolCall, ToolDescriptor, ToolOutcomeKind
from personal_assistant.infrastructure.browser.executor import (
    BROWSER_FILL_CAPABILITY,
    BROWSER_SUBMIT_CAPABILITY,
    BrowserActionExecutor,
)
from personal_assistant.infrastructure.memory import InMemorySideEffectOutbox
from personal_assistant.infrastructure.memory.browser import (
    InMemoryBrowserAdapterStore,
    InMemoryBrowserSessionStore,
)
from tests.support.browser import (
    TEST_APP_PATH,
    TEST_LOGIN_PATH,
    TEST_ORIGIN,
    FakeField,
    app_page_fingerprint,
    standard_adapter,
    standard_companion,
)

EXTENSION_ID = "nju.ehall"
EXTENSION_VERSION = "0.1.0"
ADAPTER_ID = "nju.ehall.proof"
TRANSACTION_ID = "proof.apply"
TASK_ID = "task-1"


class _Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


class ExecutorTestCase(unittest.IsolatedAsyncioTestCase):
    def make_gateway(self, *, submit_enabled: bool = True) -> None:
        self.clock = _Clock()
        self.companion = standard_companion()
        self.sessions = InMemoryBrowserSessionStore()
        self.adapters = InMemoryBrowserAdapterStore()
        self.broker = BrowserSessionBroker(
            companion=self.companion,
            sessions=self.sessions,
            adapters=self.adapters,
            allowed_origins=frozenset({TEST_ORIGIN}),
            submit_enabled=submit_enabled,
            now=self.clock.now,
            id_factory=lambda: "brs_exec",
            nonce_factory=lambda: "nonce-fixed",
        )
        self.approval_repository = InMemoryApprovalRepository()
        self.approvals = ApprovalService(self.approval_repository)
        self.outbox = InMemorySideEffectOutbox(self.approvals)
        self.executor = BrowserActionExecutor(broker=self.broker)
        self.registry = ToolRegistry()
        self.registry.publish(
            (
                ToolDescriptor(
                    id="ehall.fill_form",
                    version="0.1.0",
                    extension_id=EXTENSION_ID,
                    extension_version=EXTENSION_VERSION,
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    risk=RiskLevel.EXTERNAL_WRITE,
                    required_capabilities=frozenset({BROWSER_FILL_CAPABILITY}),
                    timeout_seconds=120,
                ),
                ToolDescriptor(
                    id="ehall.submit",
                    version="0.1.0",
                    extension_id=EXTENSION_ID,
                    extension_version=EXTENSION_VERSION,
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    risk=RiskLevel.EXTERNAL_WRITE,
                    required_capabilities=frozenset({BROWSER_SUBMIT_CAPABILITY}),
                    timeout_seconds=120,
                ),
            )
        )
        self.gateway = ToolGateway(
            registry=self.registry,
            policy=ToolPolicy(),
            approvals=self.approvals,
            executor=self.executor,
            outbox=self.outbox,
        )

    async def reach_prepared_session(self, *, variant_fields: bool = False) -> tuple[str, str]:
        if variant_fields:
            await self.broker.register_adapter(
                standard_adapter(
                    allowed_page_fingerprints=(
                        app_page_fingerprint(),
                        app_page_fingerprint(
                            extra_fields=(
                                FakeField("ctl:0:9", name="mystery", required=False),
                            )
                        ),
                    )
                )
            )
        else:
            await self.broker.register_adapter(standard_adapter())
        record = await self.broker.create_session(
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="办理在读证明",
        )
        session_id = record.session_id
        await self.broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            transaction_id=TRANSACTION_ID,
            url=TEST_ORIGIN + TEST_LOGIN_PATH,
        )
        snapshot = await self.broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            transaction_id=TRANSACTION_ID,
            url=TEST_ORIGIN + TEST_APP_PATH,
        )
        await self.broker.record_discovery(
            session_id, extension_id=EXTENSION_ID, app_count=4
        )
        await self.broker.record_preparation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            adapter_version="1.0.0",
            app_id="proof",
            transaction_id=TRANSACTION_ID,
            page_fingerprint=snapshot.fingerprint,
            planned_fields=3,
        )
        return session_id, snapshot.fingerprint

    def fill_arguments(self, session_id: str, fingerprint: str) -> dict[str, object]:
        return {
            "session_id": session_id,
            "adapter_id": ADAPTER_ID,
            "adapter_version": "1.0.0",
            "transaction_id": TRANSACTION_ID,
            "app_id": "proof",
            "expected_origin": TEST_ORIGIN,
            "expected_page_fingerprint": fingerprint,
            "consequences": "提交后进入院系审核。",
            "fields": [
                {
                    "field_id": "reason",
                    "locator": "ctl:0:0",
                    "label": "申请理由",
                    "old_value": "",
                    "new_value": "需要办理在读证明",
                    "source": "USER_INPUT",
                    "confidence": 1.0,
                },
                {
                    "field_id": "phone",
                    "locator": "ctl:0:1",
                    "label": "联系电话",
                    "old_value": "",
                    "new_value": "13800000000",
                    "source": "USER_INPUT",
                    "confidence": 1.0,
                },
                {
                    "field_id": "delivery",
                    "locator": "ctl:1:0",
                    "label": "领取方式",
                    "old_value": "paper",
                    "new_value": "paper",
                    "source": "USER_INPUT",
                    "confidence": 1.0,
                },
            ],
            "attachments": [],
        }

    def call(self, tool_id: str, arguments: dict[str, object], **kwargs: object) -> ToolCall:
        return ToolCall(
            tool_id=tool_id,
            tool_version="0.1.0",
            arguments=arguments,
            task_id=TASK_ID,
            target={"origin": TEST_ORIGIN, "transaction_id": TRANSACTION_ID},
            workflow_allowed_tools=kwargs.pop(  # type: ignore[arg-type]
                "workflow_allowed_tools",
                frozenset({"ehall.fill_form", "ehall.submit"}),
            ),
            granted_capabilities=kwargs.pop(  # type: ignore[arg-type]
                "granted_capabilities",
                frozenset({BROWSER_FILL_CAPABILITY, BROWSER_SUBMIT_CAPABILITY}),
            ),
            **kwargs,  # type: ignore[arg-type]
        )

    async def run_fill_with_approval(
        self, session_id: str, fingerprint: str
    ) -> tuple[object, dict[str, object]]:
        arguments = self.fill_arguments(session_id, fingerprint)
        first = await self.gateway.invoke(self.call("ehall.fill_form", arguments))
        self.assertEqual(first.kind, ToolOutcomeKind.APPROVAL_REQUIRED)
        assert first.approval_id is not None
        record = await self.approvals.get(first.approval_id)
        await self.approvals.approve(
            first.approval_id, nonce=record.nonce, actor_id="owner"
        )
        second = await self.gateway.invoke(
            self.call("ehall.fill_form", arguments, approval_id=first.approval_id)
        )
        return second, arguments


class FillGatewayTests(ExecutorTestCase):
    async def test_fill_requires_an_approval_before_any_page_write(self) -> None:
        self.make_gateway()
        session_id, fingerprint = await self.reach_prepared_session()
        result = await self.gateway.invoke(
            self.call("ehall.fill_form", self.fill_arguments(session_id, fingerprint))
        )
        self.assertEqual(result.kind, ToolOutcomeKind.APPROVAL_REQUIRED)
        self.assertEqual(self.companion.fill_calls, [])

    async def test_approved_fill_produces_the_preview(self) -> None:
        self.make_gateway()
        session_id, fingerprint = await self.reach_prepared_session()
        result, _ = await self.run_fill_with_approval(session_id, fingerprint)
        self.assertEqual(result.kind, ToolOutcomeKind.SUCCESS)
        document = result.result
        self.assertEqual(document["origin"], TEST_ORIGIN)
        self.assertEqual(document["transaction_id"], TRANSACTION_ID)
        self.assertEqual(len(document["canonical_payload_hash"]), 64)
        self.assertEqual(document["risk"], RiskLevel.EXTERNAL_WRITE.value)
        self.assertEqual(len(self.companion.fill_calls), 1)

    async def test_changed_payload_burns_the_approval_with_zero_writes(self) -> None:
        self.make_gateway()
        session_id, fingerprint = await self.reach_prepared_session()
        arguments = self.fill_arguments(session_id, fingerprint)
        first = await self.gateway.invoke(self.call("ehall.fill_form", arguments))
        record = await self.approvals.get(first.approval_id or "")
        await self.approvals.approve(
            first.approval_id or "", nonce=record.nonce, actor_id="owner"
        )
        mutated = self.fill_arguments(session_id, fingerprint)
        fields = mutated["fields"]
        assert isinstance(fields, list)
        fields[0]["new_value"] = "篡改后的理由"  # type: ignore[index]
        result = await self.gateway.invoke(
            self.call("ehall.fill_form", mutated, approval_id=first.approval_id)
        )
        self.assertEqual(result.kind, ToolOutcomeKind.FAILED)
        self.assertEqual(self.companion.fill_calls, [])

    async def test_expired_approval_never_fills(self) -> None:
        self.make_gateway()
        session_id, fingerprint = await self.reach_prepared_session()
        arguments = self.fill_arguments(session_id, fingerprint)
        first = await self.gateway.invoke(self.call("ehall.fill_form", arguments))
        record = await self.approvals.get(first.approval_id or "")
        await self.approvals.approve(
            first.approval_id or "", nonce=record.nonce, actor_id="owner"
        )
        approved = await self.approvals.get(first.approval_id or "")
        expired = replace(approved, expires_at=datetime(2020, 1, 1, tzinfo=UTC))
        await self.approval_repository.save(expired, expected_version=approved.version)
        result = await self.gateway.invoke(
            self.call("ehall.fill_form", arguments, approval_id=first.approval_id)
        )
        self.assertEqual(result.kind, ToolOutcomeKind.FAILED)
        self.assertEqual(self.companion.fill_calls, [])

    async def test_replayed_approval_cannot_fill_twice(self) -> None:
        self.make_gateway()
        session_id, fingerprint = await self.reach_prepared_session()
        arguments = self.fill_arguments(session_id, fingerprint)
        first = await self.gateway.invoke(self.call("ehall.fill_form", arguments))
        record = await self.approvals.get(first.approval_id or "")
        await self.approvals.approve(
            first.approval_id or "", nonce=record.nonce, actor_id="owner"
        )
        ok = await self.gateway.invoke(
            self.call("ehall.fill_form", arguments, approval_id=first.approval_id)
        )
        self.assertEqual(ok.kind, ToolOutcomeKind.SUCCESS)
        again = await self.gateway.invoke(
            self.call("ehall.fill_form", arguments, approval_id=first.approval_id)
        )
        self.assertEqual(again.kind, ToolOutcomeKind.FAILED)
        self.assertEqual(len(self.companion.fill_calls), 1)

    async def test_unknown_field_at_execution_time_blocks_fill(self) -> None:
        self.make_gateway()
        self.companion.pages[TEST_APP_PATH].fields.append(
            type(self.companion.pages[TEST_APP_PATH].fields[0])(
                locator="ctl:0:9", name="mystery", required=False
            )
        )
        session_id, _ = await self.reach_prepared_session(variant_fields=True)
        # Re-plan with the drifted page so the plan itself passes fingerprint
        # checks only if the executor fails closed first.
        arguments = self.fill_arguments(session_id, "0" * 64)
        first = await self.gateway.invoke(self.call("ehall.fill_form", arguments))
        record = await self.approvals.get(first.approval_id or "")
        await self.approvals.approve(
            first.approval_id or "", nonce=record.nonce, actor_id="owner"
        )
        result = await self.gateway.invoke(
            self.call("ehall.fill_form", arguments, approval_id=first.approval_id)
        )
        self.assertEqual(result.kind, ToolOutcomeKind.FAILED)
        self.assertEqual(self.companion.fill_calls, [])

    async def test_prohibited_tool_is_rejected_before_the_executor(self) -> None:
        self.make_gateway()
        self.registry.publish(
            (
                ToolDescriptor(
                    id="ehall.withdraw",
                    version="0.1.0",
                    extension_id=EXTENSION_ID,
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    risk=RiskLevel.PROHIBITED,
                ),
            )
        )
        result = await self.gateway.invoke(
            self.call(
                "ehall.withdraw",
                {"session_id": "brs_exec"},
                workflow_allowed_tools=frozenset({"ehall.withdraw"}),
            )
        )
        self.assertEqual(result.kind, ToolOutcomeKind.FAILED)
        self.assertIn("prohibited", (result.message or "").lower())

    async def test_missing_granted_capability_is_rejected(self) -> None:
        self.make_gateway()
        session_id, fingerprint = await self.reach_prepared_session()
        result = await self.gateway.invoke(
            self.call(
                "ehall.fill_form",
                self.fill_arguments(session_id, fingerprint),
                granted_capabilities=frozenset(),
            )
        )
        self.assertEqual(result.kind, ToolOutcomeKind.FAILED)
        self.assertEqual(self.companion.fill_calls, [])


class SubmitGatewayTests(ExecutorTestCase):
    async def reach_preview(self) -> tuple[str, dict[str, object], dict[str, object]]:
        self.make_gateway()
        session_id, fingerprint = await self.reach_prepared_session()
        result, arguments = await self.run_fill_with_approval(session_id, fingerprint)
        preview = result.result
        submit_arguments = {
            "session_id": session_id,
            "preview_hash": preview["canonical_payload_hash"],
            "preview_nonce": preview["nonce"],
            "action_id": "proof.submit",
        }
        return session_id, submit_arguments, preview

    async def approve_and_submit(
        self, arguments: dict[str, object]
    ) -> tuple[object, object]:
        first = await self.gateway.invoke(self.call("ehall.submit", arguments))
        self.assertEqual(first.kind, ToolOutcomeKind.APPROVAL_REQUIRED)
        record = await self.approvals.get(first.approval_id or "")
        await self.approvals.approve(
            first.approval_id or "", nonce=record.nonce, actor_id="owner"
        )
        second = await self.gateway.invoke(
            self.call("ehall.submit", arguments, approval_id=first.approval_id)
        )
        return first, second

    async def test_submission_requires_its_own_approval(self) -> None:
        _, arguments, _ = await self.reach_preview()
        result = await self.gateway.invoke(self.call("ehall.submit", arguments))
        self.assertEqual(result.kind, ToolOutcomeKind.APPROVAL_REQUIRED)
        self.assertEqual(self.companion.click_calls, [])

    async def test_approved_submission_succeeds_with_a_receipt(self) -> None:
        _, arguments, _ = await self.reach_preview()
        # The tracking page gains exactly this reference after the click.
        self.companion.receipt_appears_after_click = "NJU-2026-0001"
        _, result = await self.approve_and_submit(arguments)
        self.assertEqual(result.kind, ToolOutcomeKind.SUCCESS)
        self.assertEqual(result.result["state"], "SUCCEEDED")
        self.assertIn("NJU-2026-0001", result.result["reference"])
        self.assertEqual(len(self.companion.click_calls), 1)

    async def test_lost_response_is_outcome_unknown(self) -> None:
        _, arguments, _ = await self.reach_preview()
        self.companion.click_error = RuntimeError("connection reset")
        _, result = await self.approve_and_submit(arguments)
        self.assertEqual(result.kind, ToolOutcomeKind.OUTCOME_UNKNOWN)
        self.assertEqual(len(self.companion.click_calls), 1)

    async def test_companion_timeout_is_outcome_unknown(self) -> None:
        _, arguments, _ = await self.reach_preview()
        self.companion.click_error = TimeoutError("companion hung")
        _, result = await self.approve_and_submit(arguments)
        self.assertEqual(result.kind, ToolOutcomeKind.OUTCOME_UNKNOWN)
        self.assertEqual(len(self.companion.click_calls), 1)
        # The UNKNOWN session can only be resolved read-only.
        from personal_assistant.core.browser import BrowserSessionState

        record = await self.sessions.get(str(arguments["session_id"]))
        assert record is not None
        self.assertEqual(record.state, BrowserSessionState.UNKNOWN)
        retry = await self.gateway.invoke(self.call("ehall.submit", arguments))
        self.assertEqual(retry.kind, ToolOutcomeKind.APPROVAL_REQUIRED)
        self.assertEqual(len(self.companion.click_calls), 1)

    async def test_unknown_outcome_is_never_retried_by_the_gateway(self) -> None:
        _, arguments, _ = await self.reach_preview()
        self.companion.click_outcome = "UNKNOWN"
        _, result = await self.approve_and_submit(arguments)
        self.assertEqual(result.kind, ToolOutcomeKind.OUTCOME_UNKNOWN)
        self.assertEqual(len(self.companion.click_calls), 1)

    async def test_page_drift_after_the_preview_never_clicks(self) -> None:
        _, arguments, _ = await self.reach_preview()
        self.companion.pages[TEST_APP_PATH].fields[0].value = "外部改动"
        _, result = await self.approve_and_submit(arguments)
        self.assertEqual(result.kind, ToolOutcomeKind.FAILED)
        self.assertEqual(self.companion.click_calls, [])

    async def test_submission_is_disabled_by_configuration(self) -> None:
        self.make_gateway(submit_enabled=False)
        session_id, fingerprint = await self.reach_prepared_session()
        result, _ = await self.run_fill_with_approval(session_id, fingerprint)
        arguments = {
            "session_id": session_id,
            "preview_hash": result.result["canonical_payload_hash"],
            "preview_nonce": result.result["nonce"],
            "action_id": "proof.submit",
        }
        _, submitted = await self.approve_and_submit(arguments)
        self.assertEqual(submitted.kind, ToolOutcomeKind.FAILED)
        self.assertIn("SUBMIT_DISABLED", submitted.message or "")
        self.assertEqual(self.companion.click_calls, [])

    async def test_wrong_action_id_never_clicks(self) -> None:
        session_id, arguments, _ = await self.reach_preview()
        arguments = dict(arguments)
        arguments["action_id"] = "proof.cancel"
        _, result = await self.approve_and_submit(arguments)
        self.assertEqual(result.kind, ToolOutcomeKind.FAILED)
        self.assertEqual(self.companion.click_calls, [])

    async def test_server_rejection_is_a_definitive_failure(self) -> None:
        _, arguments, _ = await self.reach_preview()
        self.companion.click_outcome = "REJECTED"
        _, result = await self.approve_and_submit(arguments)
        self.assertEqual(result.kind, ToolOutcomeKind.FAILED)
        self.assertEqual(len(self.companion.click_calls), 1)


if __name__ == "__main__":
    unittest.main()
