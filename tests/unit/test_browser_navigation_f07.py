"""F07 extension: declared navigation actions open one pinned transaction."""

from __future__ import annotations

import itertools
import unittest
from datetime import UTC, datetime

from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.browser import (
    AdapterActionSpec,
    BrowserPolicyError,
    BrowserSessionBroker,
    BrowserSessionState,
    BrowserSessionStateError,
    NavigationDeniedError,
    PageDriftError,
    ProhibitedTransactionError,
    TransactionAdapterDescriptor,
    validate_adapter_descriptor,
)
from personal_assistant.core.tools import ToolGateway, ToolPolicy, ToolRegistry
from personal_assistant.domain import (
    RiskLevel,
    ToolCall,
    ToolDescriptor,
    ToolOutcomeKind,
)
from personal_assistant.domain.enums import RiskLevel as DomainRiskLevel
from personal_assistant.infrastructure.browser.executor import (
    BROWSER_NAVIGATE_CAPABILITY,
    BrowserActionExecutor,
)
from personal_assistant.infrastructure.memory import InMemorySideEffectOutbox
from personal_assistant.infrastructure.memory.browser import (
    InMemoryBrowserAdapterStore,
    InMemoryBrowserSessionStore,
)
from tests.support.browser import (
    TEST_APP_PATH,
    TEST_DISCOVERY_PATH,
    TEST_LOGIN_PATH,
    TEST_ORIGIN,
    TEST_TRANSCRIPT_PATH,
    FakeField,
    FakePage,
    app_page_fingerprint,
    standard_adapter,
    standard_companion,
    transcript_adapter,
)

EXTENSION_ID = "nju.ehall"
EXTENSION_VERSION = "0.1.0"
TASK_ID = "task-nav"


class _Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


class ValidationTests(unittest.TestCase):
    def _descriptor(self, **overrides):
        values: dict[str, object] = {
            "extension_id": EXTENSION_ID,
            "extension_version": EXTENSION_VERSION,
            "adapter_id": "nju.ehall.proof",
            "adapter_version": "1.0.0",
            "display_name": "在读证明申请",
            "allowed_origins": (TEST_ORIGIN,),
            "allowed_paths": (TEST_APP_PATH,),
            "declared_risk": DomainRiskLevel.EXTERNAL_WRITE,
            "transaction_ids": ("proof.apply",),
            "allowed_page_fingerprints": ("a" * 64,),
            "actions": (
                AdapterActionSpec(
                    action_id="proof.open",
                    locator="act:0",
                    label="在读证明申请",
                    kind="navigate",
                    risk=DomainRiskLevel.INTERNAL_WRITE,
                    transaction_id="proof.apply",
                    navigates_to_path=TEST_APP_PATH,
                ),
            ),
        }
        values.update(overrides)
        return TransactionAdapterDescriptor(**values)  # type: ignore[arg-type]

    def test_validation_accepts_a_bound_navigation_action(self) -> None:
        validate_adapter_descriptor(self._descriptor(), allowed_origins={TEST_ORIGIN})

    def test_navigation_action_cannot_be_final(self) -> None:
        descriptor = self._descriptor(
            actions=(
                AdapterActionSpec(
                    action_id="proof.open",
                    locator="act:0",
                    label="在读证明申请",
                    kind="navigate",
                    final=True,
                    transaction_id="proof.apply",
                    navigates_to_path=TEST_APP_PATH,
                ),
            )
        )
        with self.assertRaises(BrowserPolicyError):
            validate_adapter_descriptor(descriptor, allowed_origins={TEST_ORIGIN})

    def test_navigation_action_requires_a_declared_transaction(self) -> None:
        for transaction_id in ("", "unknown.apply"):
            with self.subTest(transaction_id=transaction_id):
                descriptor = self._descriptor(
                    actions=(
                        AdapterActionSpec(
                            action_id="proof.open",
                            locator="act:0",
                            label="在读证明申请",
                            kind="navigate",
                            transaction_id=transaction_id,
                            navigates_to_path=TEST_APP_PATH,
                        ),
                    )
                )
                with self.assertRaises(BrowserPolicyError):
                    validate_adapter_descriptor(descriptor, allowed_origins={TEST_ORIGIN})

    def test_navigation_action_requires_a_static_path(self) -> None:
        for path in ("", "apps/proof", "/apps/../secret", "/apps/proof?x=1", "/a\\b"):
            with self.subTest(path=path):
                descriptor = self._descriptor(
                    actions=(
                        AdapterActionSpec(
                            action_id="proof.open",
                            locator="act:0",
                            label="在读证明申请",
                            kind="navigate",
                            transaction_id="proof.apply",
                            navigates_to_path=path,
                        ),
                    )
                )
                with self.assertRaises(BrowserPolicyError):
                    validate_adapter_descriptor(descriptor, allowed_origins={TEST_ORIGIN})

    def test_only_navigation_actions_may_declare_an_entry_target(self) -> None:
        descriptor = self._descriptor(
            actions=(
                AdapterActionSpec(
                    action_id="proof.submit",
                    locator="act:0",
                    label="提交",
                    kind="submit",
                    final=True,
                    method="POST",
                    target_origin=TEST_ORIGIN,
                    target_path="/apps/proof/submit",
                    navigates_to_path=TEST_APP_PATH,
                ),
            )
        )
        with self.assertRaises(BrowserPolicyError):
            validate_adapter_descriptor(descriptor, allowed_origins={TEST_ORIGIN})


class NavigationBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.clock = _Clock()
        self.companion = standard_companion()
        self._ids = itertools.count(1)
        self.sessions = InMemoryBrowserSessionStore()
        self.adapters = InMemoryBrowserAdapterStore()
        self.broker = BrowserSessionBroker(
            companion=self.companion,
            sessions=self.sessions,
            adapters=self.adapters,
            allowed_origins=frozenset({TEST_ORIGIN}),
            submit_enabled=False,
            now=self.clock.now,
            id_factory=lambda: f"brs_nav_{next(self._ids)}",
            nonce_factory=lambda: "nonce-nav",
        )
        await self.broker.register_adapter(standard_adapter())
        await self.broker.register_adapter(transcript_adapter())

    async def reach_discovered(self) -> str:
        record = await self.broker.create_session(
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="办理事项",
        )
        session_id = record.session_id
        await self.broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id="nju.ehall.proof",
            transaction_id="",
            url=TEST_ORIGIN + TEST_LOGIN_PATH,
        )
        await self.broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id="nju.ehall.proof",
            transaction_id="",
            url=TEST_ORIGIN + TEST_DISCOVERY_PATH,
        )
        await self.broker.record_discovery(session_id, extension_id=EXTENSION_ID, app_count=2)
        return session_id

    async def open(self, session_id: str, *, transaction_id: str = "proof.apply"):
        adapter_id = (
            "nju.ehall.proof" if transaction_id == "proof.apply" else "nju.ehall.transcript"
        )
        return await self.broker.execute_navigation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=adapter_id,
            transaction_id=transaction_id,
        )

    async def test_open_transaction_routes_and_verifies_the_landing_page(self) -> None:
        session_id = await self.reach_discovered()
        result = await self.open(session_id)
        self.assertEqual(result["transaction_id"], "proof.apply")
        self.assertEqual(result["path"], TEST_APP_PATH)
        self.assertEqual(result["page_fingerprint"], app_page_fingerprint())
        self.assertEqual(result["state"], BrowserSessionState.DISCOVERED.value)
        self.assertEqual(
            self.companion.activate_calls,
            [(session_id, "act:0", TEST_APP_PATH)],
        )
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.transaction_id, "proof.apply")
        self.assertIn(TEST_APP_PATH, record.visited_paths)

    async def test_open_transaction_selects_the_right_adapter_per_transaction(self) -> None:
        session_id = await self.reach_discovered()
        result = await self.open(session_id, transaction_id="transcript.apply")
        self.assertEqual(result["transaction_id"], "transcript.apply")
        self.assertEqual(result["path"], TEST_TRANSCRIPT_PATH)
        self.assertEqual(
            self.companion.activate_calls,
            [(session_id, "act:1", TEST_TRANSCRIPT_PATH)],
        )

    async def test_open_transaction_rejects_an_unknown_transaction(self) -> None:
        session_id = await self.reach_discovered()
        with self.assertRaises(BrowserPolicyError):
            await self.open(session_id, transaction_id="withdraw.apply")
        self.assertEqual(self.companion.activate_calls, [])

    async def test_open_transaction_rejects_an_undeclared_action(self) -> None:
        await self.broker.register_adapter(
            standard_adapter(
                actions=(
                    AdapterActionSpec(
                        action_id="proof.submit",
                        locator="act:0",
                        label="提交申请",
                        kind="submit",
                        risk=RiskLevel.EXTERNAL_WRITE,
                        final=True,
                        method="POST",
                        target_origin=TEST_ORIGIN,
                        target_path="/apps/proof/submit",
                    ),
                )
            )
        )
        session_id = await self.reach_discovered()
        with self.assertRaises(BrowserPolicyError) as caught:
            await self.open(session_id)
        self.assertEqual(caught.exception.reason, "NAVIGATION_NOT_DECLARED")
        self.assertEqual(self.companion.activate_calls, [])

    async def test_open_transaction_requires_an_authenticated_or_discovered_session(self) -> None:
        session_id = await self.reach_discovered()
        await self.broker.record_preparation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id="nju.ehall.proof",
            adapter_version="1.0.0",
            app_id="proof",
            transaction_id="proof.apply",
            page_fingerprint=app_page_fingerprint(),
            planned_fields=3,
        )
        with self.assertRaises(BrowserSessionStateError):
            await self.open(session_id)
        self.assertEqual(self.companion.activate_calls, [])

    async def test_open_transaction_detects_action_label_drift(self) -> None:
        session_id = await self.reach_discovered()
        self.companion.pages[TEST_DISCOVERY_PATH].actions[0].label = "另一个事项"
        with self.assertRaises(PageDriftError):
            await self.open(session_id)
        self.assertEqual(self.companion.activate_calls, [])
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(record.diagnostic_code, "NAVIGATION_ACTION_DRIFT")

    async def test_open_transaction_detects_a_wrong_landing_path(self) -> None:
        session_id = await self.reach_discovered()
        self.companion.activate_landing = "/portal"
        with self.assertRaises(PageDriftError):
            await self.open(session_id)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(record.diagnostic_code, "NAVIGATION_MISMATCH")

    async def test_open_transaction_detects_an_unpinned_landing_fingerprint(self) -> None:
        companion = standard_companion()
        companion.pages[TEST_TRANSCRIPT_PATH] = FakePage(
            TEST_TRANSCRIPT_PATH,
            title="研究生成绩单打印",
            heading="在读证明申请",
            fields=[
                FakeField("ctl:0:0", name="reason", required=True, max_length=200),
                FakeField("ctl:0:1", name="phone", required=True, max_length=20),
                FakeField("ctl:0:2", name="mystery", required=False),
                FakeField(
                    "ctl:1:0",
                    kind="select",
                    value="paper",
                    name="delivery",
                    required=True,
                    options=("paper", "email"),
                    tag="select",
                ),
            ],
            actions=[],
        )
        self.companion = companion
        self.broker = BrowserSessionBroker(
            companion=companion,
            sessions=self.sessions,
            adapters=self.adapters,
            allowed_origins=frozenset({TEST_ORIGIN}),
            submit_enabled=False,
            now=self.clock.now,
            id_factory=lambda: f"brs_nav_{next(self._ids)}",
            nonce_factory=lambda: "nonce-nav",
        )
        await self.broker.register_adapter(standard_adapter())
        await self.broker.register_adapter(transcript_adapter())
        session_id = await self.reach_discovered()
        with self.assertRaises(ProhibitedTransactionError) as caught:
            await self.open(session_id, transaction_id="transcript.apply")
        self.assertEqual(caught.exception.reason, "UNKNOWN_PAGE_VERSION")
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)

    async def test_open_transaction_aborts_when_a_page_write_is_blocked(self) -> None:
        session_id = await self.reach_discovered()
        self.companion.activate_blocked_writes = 1
        with self.assertRaises(BrowserPolicyError) as caught:
            await self.open(session_id)
        self.assertEqual(caught.exception.reason, "NAVIGATION_WRITE_BLOCKED")
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(record.diagnostic_code, "NAVIGATION_WRITE_BLOCKED")

    async def test_open_transaction_fails_closed_on_an_off_allowlist_landing(self) -> None:
        session_id = await self.reach_discovered()
        self.companion.activate_landing = "https://evil.example.com/apps/proof"
        with self.assertRaises((NavigationDeniedError, PageDriftError)):
            await self.open(session_id)
        self.assertEqual(self.companion.click_calls, [])


class NavigationExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.companion = standard_companion()
        self._ids = itertools.count(1)
        self.broker = BrowserSessionBroker(
            companion=self.companion,
            sessions=InMemoryBrowserSessionStore(),
            adapters=InMemoryBrowserAdapterStore(),
            allowed_origins=frozenset({TEST_ORIGIN}),
            submit_enabled=False,
            id_factory=lambda: f"brs_exec_{next(self._ids)}",
            nonce_factory=lambda: "nonce-exec",
        )
        await self.broker.register_adapter(standard_adapter())
        self.registry = ToolRegistry()
        self.registry.publish(
            (
                ToolDescriptor(
                    id="ehall.open_transaction",
                    version="0.1.0",
                    extension_id=EXTENSION_ID,
                    extension_version=EXTENSION_VERSION,
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    risk=RiskLevel.INTERNAL_WRITE,
                    required_capabilities=frozenset({BROWSER_NAVIGATE_CAPABILITY}),
                    timeout_seconds=60,
                ),
            )
        )
        self.approvals = ApprovalService(InMemoryApprovalRepository())
        self.gateway = ToolGateway(
            registry=self.registry,
            policy=ToolPolicy(),
            approvals=self.approvals,
            executor=BrowserActionExecutor(broker=self.broker),
            outbox=InMemorySideEffectOutbox(self.approvals),
        )

    async def test_executor_routes_browser_navigate_tools(self) -> None:
        record = await self.broker.create_session(
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="办理事项",
        )
        session_id = record.session_id
        await self.broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id="nju.ehall.proof",
            transaction_id="",
            url=TEST_ORIGIN + TEST_LOGIN_PATH,
        )
        await self.broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id="nju.ehall.proof",
            transaction_id="",
            url=TEST_ORIGIN + TEST_DISCOVERY_PATH,
        )
        await self.broker.record_discovery(session_id, extension_id=EXTENSION_ID, app_count=2)
        result = await self.gateway.invoke(
            ToolCall(
                tool_id="ehall.open_transaction",
                tool_version="0.1.0",
                task_id=TASK_ID,
                arguments={
                    "session_id": session_id,
                    "adapter_id": "nju.ehall.proof",
                    "transaction_id": "proof.apply",
                },
                target={"origin": TEST_ORIGIN, "transaction_id": "proof.apply"},
                workflow_allowed_tools=frozenset({"ehall.open_transaction"}),
                granted_capabilities=frozenset({BROWSER_NAVIGATE_CAPABILITY}),
            )
        )
        self.assertEqual(result.kind, ToolOutcomeKind.SUCCESS)
        assert result.result is not None
        self.assertEqual(result.result["path"], TEST_APP_PATH)


if __name__ == "__main__":
    unittest.main()
