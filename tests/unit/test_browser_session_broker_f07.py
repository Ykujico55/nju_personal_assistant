"""Gate 1/F07: browser session broker state machine and fail-closed behaviour."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from personal_assistant.core.browser import (
    AdapterActionSpec,
    AttachmentPreview,
    BrowserLimitError,
    BrowserPolicyError,
    BrowserSessionBroker,
    BrowserSessionState,
    BrowserSessionStateError,
    FieldChange,
    FieldValueSource,
    FillPlan,
    NavigationDeniedError,
    PageDriftError,
    PreviewExpiredError,
    ProhibitedTransactionError,
)
from personal_assistant.domain.enums import RiskLevel
from personal_assistant.infrastructure.memory.browser import (
    InMemoryBrowserAdapterStore,
    InMemoryBrowserSessionStore,
)
from tests.support.browser import (
    TEST_APP_PATH,
    TEST_LOGIN_PATH,
    TEST_ORIGIN,
    TEST_SUBMIT_PATH,
    TEST_TRACKING_PATH,
    FakeCompanion,
    FakeField,
    app_page_fingerprint,
    standard_adapter,
    standard_companion,
    standard_plan,
)

EXTENSION_ID = "nju.ehall"
EXTENSION_VERSION = "0.1.0"
ADAPTER_ID = "nju.ehall.proof"
TRANSACTION_ID = "proof.apply"
TASK_ID = "task-0001"


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


class BrokerTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.make_broker()

    def make_broker(
        self,
        companion: FakeCompanion | None = None,
        *,
        submit_enabled: bool = False,
        allowed_origins: frozenset[str] | None = None,
    ) -> tuple[BrowserSessionBroker, FakeCompanion, FakeClock]:
        import itertools

        self._session_ids = itertools.count(1)
        self.companion = companion or standard_companion()
        self.clock = FakeClock()
        self.sessions = InMemoryBrowserSessionStore()
        self.adapters = InMemoryBrowserAdapterStore()
        self.broker = BrowserSessionBroker(
            companion=self.companion,
            sessions=self.sessions,
            adapters=self.adapters,
            allowed_origins=allowed_origins
            if allowed_origins is not None
            else frozenset({TEST_ORIGIN}),
            submit_enabled=submit_enabled,
            now=self.clock.now,
            id_factory=lambda: next(self._session_ids),
            nonce_factory=lambda: "nonce-fixed",
        )
        return self.broker, self.companion, self.clock

    async def register(self, *, variant_fields: bool = False) -> None:
        if variant_fields:
            descriptor = standard_adapter(
                allowed_page_fingerprints=(
                    app_page_fingerprint(),
                    app_page_fingerprint(
                        extra_fields=(
                            FakeField("ctl:0:2", name="mystery", required=False),
                        )
                    ),
                )
            )
        else:
            descriptor = standard_adapter()
        await self.broker.register_adapter(descriptor)

    async def create(self, *, task_id: str = TASK_ID) -> str:
        record = await self.broker.create_session(
            task_id=task_id,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="办理在读证明",
        )
        return record.session_id

    async def navigate(self, session_id: str, url: str) -> object:
        return await self.broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            transaction_id=TRANSACTION_ID,
            url=url,
        )

    async def reach_prepared(self, *, task_id: str = TASK_ID) -> tuple[str, str]:
        """Return ``(session_id, page_fingerprint)`` after login and preparation."""

        session_id = await self.create(task_id=task_id)
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        snapshot = await self.navigate(session_id, TEST_ORIGIN + TEST_APP_PATH)
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
            page_fingerprint=snapshot.fingerprint,  # type: ignore[attr-defined]
            planned_fields=3,
        )
        return session_id, snapshot.fingerprint  # type: ignore[attr-defined]


class WaitingUserTests(BrokerTestCase):
    """Eight independent WAITING_USER / user-authentication scenarios."""

    async def test_new_session_starts_requested(self) -> None:
        await self.register()
        session_id = await self.create()
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.REQUESTED)

    async def test_login_page_enters_waiting_user(self) -> None:
        await self.register()
        session_id = await self.create()
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.WAITING_USER)

    async def test_login_page_from_authenticated_returns_to_waiting_user(self) -> None:
        await self.register()
        session_id, _ = await self.reach_prepared()
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.WAITING_USER)

    async def test_user_completion_moves_to_authenticated(self) -> None:
        await self.register()
        session_id = await self.create()
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        await self.navigate(session_id, TEST_ORIGIN + TEST_APP_PATH)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.AUTHENTICATED)

    async def test_snapshot_is_refused_while_waiting_for_the_user(self) -> None:
        await self.register()
        session_id = await self.create()
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        with self.assertRaises(BrowserSessionStateError):
            await self.broker.snapshot(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(self.companion.fill_calls, [])
        self.assertEqual(self.companion.click_calls, [])

    async def test_fill_is_refused_while_waiting_for_the_user(self) -> None:
        await self.register()
        session_id = await self.create()
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        plan = standard_plan(expected_fingerprint="a" * 64)
        with self.assertRaises(BrowserSessionStateError):
            await self.broker.execute_fill(
                session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
            )
        self.assertEqual(self.companion.fill_calls, [])

    async def test_submit_is_refused_while_waiting_for_the_user(self) -> None:
        await self.register()
        session_id = await self.create()
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        with self.assertRaises(BrowserSessionStateError):
            await self.broker.execute_submit(
                session_id,
                task_id=TASK_ID,
                extension_id=EXTENSION_ID,
                preview_hash="a" * 64,
                preview_nonce="nonce-fixed",
                action_id="proof.submit",
            )
        self.assertEqual(self.companion.click_calls, [])

    async def test_discovery_is_refused_while_waiting_for_the_user(self) -> None:
        await self.register()
        session_id = await self.create()
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        with self.assertRaises(BrowserSessionStateError):
            await self.broker.record_discovery(
                session_id, extension_id=EXTENSION_ID, app_count=1
            )

    async def test_status_reports_login_page_without_side_effects(self) -> None:
        await self.register()
        session_id = await self.create()
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        _, status = await self.broker.session_status(session_id, extension_id=EXTENSION_ID)
        self.assertTrue(status.login_page)
        self.assertEqual(status.fill_operations, 0)
        self.assertEqual(status.click_operations, 0)
        self.assertFalse(status.headless)

    async def test_expired_session_is_released_and_replaced(self) -> None:
        await self.register()
        session_id = await self.create()
        self.clock.advance(1801)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.CANCELLED)
        self.assertEqual(record.outcome, "EXPIRED")
        self.assertEqual(record.diagnostic_code, "SESSION_EXPIRED")
        replacement = await self.create()
        self.assertNotEqual(replacement, session_id)

    async def test_expired_session_closes_the_companion_session(self) -> None:
        await self.register()
        session_id = await self.create()
        self.clock.advance(1801)
        await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertIn(session_id, self.companion.closed)
        with self.assertRaises(BrowserSessionStateError):
            await self.broker.snapshot(session_id, extension_id=EXTENSION_ID)

    async def test_expired_session_blocks_fill_and_submit(self) -> None:
        await self.register()
        session_id, fingerprint = await self.reach_prepared()
        plan = standard_plan(expected_fingerprint=fingerprint)
        self.clock.advance(1801)
        with self.assertRaises(BrowserSessionStateError):
            await self.broker.execute_fill(
                session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
            )
        with self.assertRaises(BrowserSessionStateError):
            await self.broker.execute_submit(
                session_id,
                task_id=TASK_ID,
                extension_id=EXTENSION_ID,
                preview_hash="a" * 64,
                preview_nonce="nonce-fixed",
                action_id="proof.submit",
            )
        self.assertEqual(self.companion.fill_calls, [])
        self.assertEqual(self.companion.click_calls, [])

    async def test_executing_session_is_not_ttl_expired_mid_transition(self) -> None:
        await self.register()
        session_id = await self.create()
        record = await self.sessions.get(session_id)
        assert record is not None
        await self.sessions.save(
            replace(record, state=BrowserSessionState.EXECUTING),
            expected_version=record.version,
        )
        self.clock.advance(1801)
        still = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(BrowserSessionState.EXECUTING, still.state)

    async def test_unknown_page_version_is_refused_with_zero_writes(self) -> None:
        self.make_broker(companion=standard_companion(unknown_field=True))
        await self.register()
        session_id = await self.create()
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        with self.assertRaises(ProhibitedTransactionError) as caught:
            await self.navigate(session_id, TEST_ORIGIN + TEST_APP_PATH)
        self.assertEqual(caught.exception.reason, "UNKNOWN_PAGE_VERSION")
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(record.fill_count, 0)
        self.assertEqual(self.companion.fill_calls, [])
        self.assertEqual(self.companion.click_calls, [])

    async def test_incomplete_text_scan_is_refused_with_zero_writes(self) -> None:
        companion = standard_companion()
        self.make_broker(companion=companion)
        await self.register()
        session_id = await self.create()
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        companion.scan_incomplete = True
        with self.assertRaises(ProhibitedTransactionError) as caught:
            await self.navigate(session_id, TEST_ORIGIN + TEST_APP_PATH)
        self.assertEqual(caught.exception.reason, "PAGE_TEXT_SCAN_INCOMPLETE")
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(self.companion.fill_calls, [])

    async def test_page_version_pinning_does_not_apply_to_login_pages(self) -> None:
        await self.register()
        session_id = await self.create()
        snapshot = await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        self.assertEqual(snapshot.url, TEST_ORIGIN + TEST_LOGIN_PATH)  # type: ignore[attr-defined]
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.WAITING_USER)


class SessionLifecycleTests(BrokerTestCase):
    async def test_one_active_session_per_task_is_reused(self) -> None:
        await self.register()
        first = await self.create()
        second = await self.create()
        self.assertEqual(first, second)
        self.assertEqual(len(self.companion.navigations), 0)

    async def test_other_extension_cannot_take_over_the_session(self) -> None:
        await self.register()
        session_id = await self.create()
        with self.assertRaises(BrowserSessionStateError):
            await self.broker.get_session(session_id, extension_id="other.extension")

    async def test_renavigation_limit_is_enforced(self) -> None:
        await self.register()
        session_id = await self.create()
        await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        for _ in range(3):
            await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        with self.assertRaises(BrowserLimitError):
            await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        self.assertEqual(len(self.companion.navigations), 4)

    async def test_non_allowlisted_origin_never_reaches_the_companion(self) -> None:
        await self.register()
        session_id = await self.create()
        with self.assertRaises(NavigationDeniedError):
            await self.navigate(session_id, "https://evil.example.com/apps/proof")
        self.assertEqual(self.companion.navigations, [])

    async def test_a_redirect_to_an_off_allowlist_origin_is_denied(self) -> None:
        await self.register()
        session_id = await self.create()
        self.companion.redirect_to = "https://evil.example.com/steal"
        with self.assertRaises(NavigationDeniedError):
            await self.navigate(session_id, TEST_ORIGIN + TEST_LOGIN_PATH)
        self.assertEqual(self.companion.fill_calls, [])
        self.assertEqual(self.companion.click_calls, [])

    async def test_session_creation_without_origins_fails_closed(self) -> None:
        broker, _, _ = self.make_broker(allowed_origins=frozenset())
        with self.assertRaises(BrowserPolicyError):
            await broker.create_session(
                task_id="t",
                extension_id=EXTENSION_ID,
                extension_version=EXTENSION_VERSION,
                purpose="x",
            )
        self.assertEqual(self.companion.navigations, [])

    async def test_adapter_outside_user_allowlist_is_rejected(self) -> None:
        self.make_broker()
        with self.assertRaises(BrowserPolicyError) as caught:
            await self.broker.register_adapter(
                standard_adapter(allowed_origins=("https://evil.example.com",))
            )
        self.assertEqual(caught.exception.reason, "ORIGIN_NOT_ALLOWED")

    async def test_close_transitions_to_cancelled_and_closes_companion(self) -> None:
        await self.register()
        session_id = await self.create()
        await self.broker.close_session(session_id, extension_id=EXTENSION_ID)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.CANCELLED)
        self.assertEqual(self.companion.closed, [session_id])

    async def test_cancel_transitions_to_cancelled(self) -> None:
        await self.register()
        session_id = await self.create()
        await self.broker.cancel_session(session_id, extension_id=EXTENSION_ID)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.CANCELLED)
        self.assertEqual(self.companion.cancelled, [session_id])

    async def test_executing_sessions_recover_to_unknown(self) -> None:
        from dataclasses import replace

        await self.register()
        session_id, _ = await self.reach_prepared()
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        executing = replace(record, state=BrowserSessionState.EXECUTING)
        await self.sessions.save(executing, expected_version=record.version)
        recovered = await self.broker.recover_stale_sessions()
        self.assertEqual(recovered, (session_id,))
        after = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(after.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(after.diagnostic_code, "EXECUTION_INTERRUPTED")


class FillTests(BrokerTestCase):
    async def test_fill_produces_a_bound_preview_and_stops(self) -> None:
        await self.register()
        session_id, fingerprint = await self.reach_prepared()
        plan = standard_plan(expected_fingerprint=fingerprint)
        preview = await self.broker.execute_fill(
            session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
        )
        self.assertEqual(preview.origin, TEST_ORIGIN)
        self.assertEqual(preview.transaction_id, TRANSACTION_ID)
        self.assertEqual(preview.page_fingerprint, fingerprint)
        values = {item.field_id: item.new_value for item in preview.fields}
        self.assertEqual(values["reason"], "需要办理在读证明")
        self.assertEqual(values["phone"], "13800000000")
        self.assertEqual(preview.missing_fields, ())
        self.assertEqual(len(preview.canonical_payload_hash), 64)
        self.assertEqual(
            self.companion.fill_calls[0][1],
            (("ctl:0:0", "需要办理在读证明"), ("ctl:0:1", "13800000000")),
        )
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.PREVIEW_READY)
        self.assertEqual(record.fill_count, 1)
        self.assertEqual(record.preview_hash, preview.canonical_payload_hash)

    async def test_repeated_fill_with_the_same_plan_returns_the_stored_preview(self) -> None:
        await self.register()
        session_id, fingerprint = await self.reach_prepared()
        plan = standard_plan(expected_fingerprint=fingerprint)
        first = await self.broker.execute_fill(
            session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
        )
        second = await self.broker.execute_fill(
            session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
        )
        self.assertEqual(first.canonical_payload_hash, second.canonical_payload_hash)
        self.assertEqual(len(self.companion.fill_calls), 1)

    async def test_unknown_field_blocks_fill_with_zero_page_writes(self) -> None:
        self.make_broker(companion=standard_companion(unknown_field=True))
        await self.register(variant_fields=True)
        session_id, fingerprint = await self.reach_prepared()
        plan = standard_plan(expected_fingerprint=fingerprint)
        with self.assertRaises(BrowserPolicyError) as caught:
            await self.broker.execute_fill(
                session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
            )
        self.assertEqual(caught.exception.reason, "UNKNOWN_FIELD")
        self.assertEqual(self.companion.fill_calls, [])
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)

    async def test_high_risk_terms_block_fill_with_zero_page_writes(self) -> None:
        self.make_broker(companion=standard_companion(signal_high_risk=True))
        await self.register()
        session_id, fingerprint = await self.reach_prepared()
        plan = standard_plan(expected_fingerprint=fingerprint)
        with self.assertRaises(ProhibitedTransactionError):
            await self.broker.execute_fill(
                session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
            )
        self.assertEqual(self.companion.fill_calls, [])
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)

    async def test_fingerprint_drift_blocks_fill_with_zero_page_writes(self) -> None:
        await self.register()
        session_id, _ = await self.reach_prepared()
        plan = standard_plan(expected_fingerprint="f" * 64)
        with self.assertRaises(PageDriftError):
            await self.broker.execute_fill(
                session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
            )
        self.assertEqual(self.companion.fill_calls, [])
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)

    async def test_live_value_drift_blocks_fill_with_zero_page_writes(self) -> None:
        await self.register()
        session_id, fingerprint = await self.reach_prepared()
        plan = standard_plan(expected_fingerprint=fingerprint, reason_old="旧的页面值")
        with self.assertRaises(PageDriftError):
            await self.broker.execute_fill(
                session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
            )
        self.assertEqual(self.companion.fill_calls, [])

    async def test_missing_required_field_blocks_fill(self) -> None:
        await self.register()
        session_id, fingerprint = await self.reach_prepared()
        base = standard_plan(expected_fingerprint=fingerprint)
        plan = FillPlan(
            adapter_id=base.adapter_id,
            adapter_version=base.adapter_version,
            transaction_id=base.transaction_id,
            expected_origin=base.expected_origin,
            expected_page_fingerprint=base.expected_page_fingerprint,
            fields=tuple(item for item in base.fields if item.field_id != "phone"),
        )
        with self.assertRaises(BrowserPolicyError) as caught:
            await self.broker.execute_fill(
                session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
            )
        self.assertEqual(caught.exception.reason, "MISSING_REQUIRED_FIELD")
        self.assertEqual(self.companion.fill_calls, [])

    async def test_invalid_pattern_blocks_fill(self) -> None:
        await self.register()
        session_id, fingerprint = await self.reach_prepared()
        plan = standard_plan(expected_fingerprint=fingerprint, phone_new="123")
        with self.assertRaises(BrowserPolicyError) as caught:
            await self.broker.execute_fill(
                session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
            )
        self.assertEqual(caught.exception.reason, "INVALID_FIELD_VALUE")
        self.assertEqual(self.companion.fill_calls, [])

    async def test_unknown_transaction_blocks_fill(self) -> None:
        await self.register()
        session_id, fingerprint = await self.reach_prepared()
        plan = standard_plan(expected_fingerprint=fingerprint)
        unknown = FillPlan(
            adapter_id=plan.adapter_id,
            adapter_version=plan.adapter_version,
            transaction_id="proof.unknown",
            expected_origin=plan.expected_origin,
            expected_page_fingerprint=plan.expected_page_fingerprint,
            fields=plan.fields,
        )
        with self.assertRaises(BrowserPolicyError) as caught:
            await self.broker.execute_fill(
                session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=unknown
            )
        self.assertEqual(caught.exception.reason, "UNKNOWN_TRANSACTION")
        self.assertEqual(self.companion.fill_calls, [])

    async def test_attachment_hashes_are_part_of_the_preview(self) -> None:
        from personal_assistant.core.browser import canonical_preview_sha256

        await self.register()
        session_id, fingerprint = await self.reach_prepared()
        attachment = AttachmentPreview(name="id.pdf", size_bytes=2048, sha256="c" * 64)
        plan = standard_plan(expected_fingerprint=fingerprint, attachments=(attachment,))
        preview = await self.broker.execute_fill(
            session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
        )
        expected = canonical_preview_sha256(
            origin=preview.origin,
            app_id=preview.app_id,
            transaction_id=preview.transaction_id,
            adapter_id=preview.adapter_id,
            adapter_version=preview.adapter_version,
            extension_id=preview.extension_id,
            extension_version=preview.extension_version,
            page_fingerprint=preview.page_fingerprint,
            risk=preview.risk,
            consequences=preview.consequences,
            fields=preview.fields,
            attachments=preview.attachments,
            target_action_id=preview.target_action_id,
            target_method=preview.target_method,
            target_origin=preview.target_origin,
            target_path=preview.target_path,
        )
        self.assertEqual(preview.canonical_payload_hash, expected)
        without = canonical_preview_sha256(
            origin=preview.origin,
            app_id=preview.app_id,
            transaction_id=preview.transaction_id,
            adapter_id=preview.adapter_id,
            adapter_version=preview.adapter_version,
            extension_id=preview.extension_id,
            extension_version=preview.extension_version,
            page_fingerprint=preview.page_fingerprint,
            risk=preview.risk,
            consequences=preview.consequences,
            fields=preview.fields,
            attachments=(),
            target_action_id=preview.target_action_id,
            target_method=preview.target_method,
            target_origin=preview.target_origin,
            target_path=preview.target_path,
        )
        self.assertNotEqual(preview.canonical_payload_hash, without)


class SubmitTests(BrokerTestCase):
    async def reach_preview(self, *, submit_enabled: bool = True) -> tuple[str, str, str]:
        self.make_broker(submit_enabled=submit_enabled)
        await self.register()
        session_id, fingerprint = await self.reach_prepared()
        preview = await self.broker.execute_fill(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            plan=standard_plan(expected_fingerprint=fingerprint),
        )
        return session_id, preview.canonical_payload_hash, preview.nonce

    async def submit(self, session_id: str, preview_hash: str, nonce: str) -> object:
        return await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview_hash,
            preview_nonce=nonce,
            action_id="proof.submit",
        )

    async def test_submit_is_disabled_by_default(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview(submit_enabled=False)
        with self.assertRaises(BrowserPolicyError) as caught:
            await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(caught.exception.reason, "SUBMIT_DISABLED")
        self.assertEqual(self.companion.click_calls, [])

    async def test_submit_uses_the_declared_target(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(
            self.companion.click_targets, [("POST", TEST_ORIGIN, TEST_SUBMIT_PATH)]
        )

    async def test_changed_submit_target_never_clicks(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
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
                        target_path="/apps/proof/other",
                    ),
                )
            )
        )
        with self.assertRaises(PageDriftError):
            await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(self.companion.click_calls, [])
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(record.diagnostic_code, "SUBMIT_TARGET_DRIFT")

    async def test_preview_hash_binds_the_submit_target(self) -> None:
        from personal_assistant.core.browser import canonical_preview_sha256

        session_id, preview_hash, nonce = await self.reach_preview()
        preview = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        assert preview.preview is not None
        document = dict(preview.preview)
        self.assertEqual(document["target_method"], "POST")
        self.assertEqual(document["target_path"], TEST_SUBMIT_PATH)
        baseline = dict(
            origin=document["origin"],
            app_id=document["app_id"],
            transaction_id=document["transaction_id"],
            adapter_id=document["adapter_id"],
            adapter_version=document["adapter_version"],
            extension_id=document["extension_id"],
            extension_version=document["extension_version"],
            page_fingerprint=document["page_fingerprint"],
            risk=RiskLevel(document["risk"]),
            consequences=document["consequences"],
            fields=tuple(
                FieldChange(
                    field_id=item["field_id"],
                    locator=item["locator"],
                    label=item["label"],
                    old_value=item["old_value"],
                    new_value=item["new_value"],
                    source=FieldValueSource(item["source"]),
                )
                for item in document["fields"]
            ),
            attachments=(),
            target_action_id=document["target_action_id"],
            target_method=document["target_method"],
            target_origin=document["target_origin"],
            target_path=document["target_path"],
        )
        self.assertEqual(canonical_preview_sha256(**baseline), preview_hash)
        changed = dict(baseline, target_path="/apps/proof/other")
        self.assertNotEqual(canonical_preview_sha256(**changed), preview_hash)

    async def test_enabled_submit_leaves_a_receipt(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        # Success is proven by the tracking page diff, never by DOM text.
        self.companion.receipt_appears_after_click = "NJU-2026-0001"
        outcome = await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(outcome.state, BrowserSessionState.SUCCEEDED)  # type: ignore[attr-defined]
        self.assertIn("NJU-2026-0001", outcome.reference)  # type: ignore[attr-defined]
        self.assertEqual(len(self.companion.click_calls), 1)

    async def test_wrong_preview_hash_never_clicks(self) -> None:
        session_id, _, nonce = await self.reach_preview()
        with self.assertRaises(PreviewExpiredError):
            await self.submit(session_id, "f" * 64, nonce)
        self.assertEqual(self.companion.click_calls, [])

    async def test_wrong_nonce_never_clicks(self) -> None:
        session_id, preview_hash, _ = await self.reach_preview()
        with self.assertRaises(PreviewExpiredError):
            await self.submit(session_id, preview_hash, "wrong-nonce")
        self.assertEqual(self.companion.click_calls, [])

    async def test_expired_preview_never_clicks(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        self.clock.advance(301)
        with self.assertRaises(PreviewExpiredError):
            await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(self.companion.click_calls, [])

    async def test_page_drift_after_preview_never_clicks(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        self.companion.pages[TEST_APP_PATH].fields[0].value = "外部改动"
        with self.assertRaises(PageDriftError):
            await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(self.companion.click_calls, [])
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)

    async def test_lost_click_outcome_is_unknown_and_never_retried(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        self.companion.click_error = RuntimeError("connection lost after click")
        outcome = await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(outcome.state, BrowserSessionState.UNKNOWN)  # type: ignore[attr-defined]
        self.assertEqual(len(self.companion.click_calls), 1)
        with self.assertRaises(BrowserSessionStateError):
            await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(len(self.companion.click_calls), 1)

    async def test_server_rejection_is_definitive_failure(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        self.companion.click_outcome = "REJECTED"
        outcome = await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(outcome.state, BrowserSessionState.FAILED)  # type: ignore[attr-defined]
        self.assertEqual(len(self.companion.click_calls), 1)

    async def test_unknown_click_outcome_stays_unknown(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        self.companion.click_outcome = "UNKNOWN"
        outcome = await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(outcome.state, BrowserSessionState.UNKNOWN)  # type: ignore[attr-defined]

    async def test_reconciliation_reads_the_tracking_page_and_issues_a_proof(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        self.companion.click_outcome = "UNKNOWN"
        await self.submit(session_id, preview_hash, nonce)
        self.companion.tracking_matches = ["NJU-2026-0002"]
        record = await self.broker.reconcile(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SUCCEEDED)
        self.assertIsNotNone(record.receipt)
        assert record.receipt is not None
        self.assertEqual(record.receipt["reference"], "NJU-2026-0002")
        self.assertEqual(record.receipt["issued_by"], "host_tracking")
        self.assertEqual(record.diagnostic_code, "RECONCILED")

    async def test_reconciliation_ignores_a_receipt_seen_before_the_click(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        # The tracking page already holds an old receipt: the baseline is read
        # from that page before the click and stored as hashes only.
        self.companion.tracking_matches = ["NJU-2026-0001"]
        self.companion.click_outcome = "UNKNOWN"
        await self.submit(session_id, preview_hash, nonce)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertIn('"captured":true', record.receipt_baseline)
        self.assertNotIn("NJU-2026-0001", record.receipt_baseline)
        # The baseline came from the real tracking page, read in a short-lived
        # tab without touching the supervised form page.
        self.assertEqual(
            self.companion.collect_urls, [TEST_ORIGIN + TEST_TRACKING_PATH]
        )
        record = await self.broker.reconcile(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(record.diagnostic_code, "RECONCILE_NOT_FOUND")

    async def test_a_truncated_baseline_never_claims_matched(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        self.companion.tracking_truncated = True
        self.companion.click_outcome = "UNKNOWN"
        await self.submit(session_id, preview_hash, nonce)
        self.companion.tracking_matches = ["NJU-2026-0009"]
        record = await self.broker.reconcile(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(record.diagnostic_code, "RECONCILE_UNSAFE")

    async def test_a_dom_only_receipt_never_counts_as_success(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        # The page "shows" a fresh receipt but no bound write was released.
        self.companion.click_write_requests = 0
        self.companion.click_outcome = "RECEIPT"
        outcome = await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(outcome.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(len(self.companion.click_calls), 1)

    async def test_reconciliation_not_found_keeps_unknown(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        self.companion.click_outcome = "UNKNOWN"
        await self.submit(session_id, preview_hash, nonce)
        record = await self.broker.reconcile(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(record.diagnostic_code, "RECONCILE_NOT_FOUND")

    async def test_ambiguous_reconciliation_stays_unknown(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        self.companion.click_outcome = "UNKNOWN"
        await self.submit(session_id, preview_hash, nonce)
        self.companion.tracking_matches = ["NJU-2026-0002", "NJU-2026-0003"]
        record = await self.broker.reconcile(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(record.diagnostic_code, "RECONCILE_AMBIGUOUS")

    async def test_reconciliation_is_only_allowed_from_unknown(self) -> None:
        session_id, preview_hash, nonce = await self.reach_preview()
        self.companion.receipt_appears_after_click = "NJU-2026-0001"
        outcome = await self.submit(session_id, preview_hash, nonce)
        self.assertEqual(outcome.state, BrowserSessionState.SUCCEEDED)  # type: ignore[attr-defined]
        with self.assertRaises(BrowserSessionStateError):
            await self.broker.reconcile(session_id, extension_id=EXTENSION_ID)

    async def test_tracking_path_is_reachable_for_read_only_reconciliation(self) -> None:
        await self.register()
        session_id, _ = await self.reach_prepared()
        snapshot, _ = await self.broker.snapshot(
            session_id, extension_id=EXTENSION_ID, adapter_id=ADAPTER_ID
        )
        self.assertEqual(snapshot.origin, TEST_ORIGIN)
        decision = await self.broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            transaction_id=TRANSACTION_ID,
            url=TEST_ORIGIN + TEST_TRACKING_PATH,
        )
        self.assertIn(TEST_TRACKING_PATH, decision.url)


if __name__ == "__main__":
    unittest.main()
