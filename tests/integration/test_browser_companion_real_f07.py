"""F07 Gate 2: a real headed Playwright browser behind the Desktop Companion.

These tests never touch the real ehall.  A local HTTPS mock site provides the
login challenge, the low-risk form and the flow-tracking page; the test "user"
drives the visible browser over CDP exactly as a human would (the system never
types a password and never clicks a login button).

Skipped only when Playwright is not installed or ``PA_TEST_BROWSER=0``.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

try:
    import playwright  # noqa: F401

    PLAYWRIGHT_AVAILABLE = True
except Exception:  # pragma: no cover - optional extra
    PLAYWRIGHT_AVAILABLE = False

from personal_assistant.bootstrap import build_container
from personal_assistant.core.browser import (
    AdapterActionSpec,
    AdapterFieldSpec,
    AttachmentPreview,
    BrowserPolicyError,
    BrowserSessionState,
    BrowserSessionStateError,
    BrowserUnavailableError,
    FieldChange,
    FieldValueSource,
    FillPlan,
    NavigationDeniedError,
    TransactionAdapterDescriptor,
)
from personal_assistant.domain.enums import RiskLevel
from personal_assistant.infrastructure.browser.companion_client import LoopbackCompanionClient
from personal_assistant.infrastructure.browser.host import (
    HOST_BROWSER_SNAPSHOT,
    BrowserCapabilityContext,
)
from personal_assistant.settings import Settings
from tests.support.companion_process import pid_alive, start_companion
from tests.support.mail_servers import generate_tls_pair
from tests.support.mock_ehall_site import (
    APP_PAGE_FINGERPRINT,
    APP_PATH,
    FRAME_SHELL_PATH,
    LOGIN_PATH,
    POPUP_PORTAL_PATH,
    PORTAL_PATH,
    STATUS_PATH,
    TEST_PASSWORD,
    TEST_USERNAME,
    MockEhallSite,
)

EXTENSION_ID = "nju.ehall"
EXTENSION_VERSION = "0.1.0"
ADAPTER_ID = "nju.ehall.proof"
ADAPTER_VERSION = "1.0.0"
TRANSACTION_ID = "proof.apply"
TASK_ID = "task-browser-1"

BROWSER_TESTS = os.getenv("PA_TEST_BROWSER", "1") != "0"


def _adapter(
    origin: str,
    fingerprints: tuple[str, ...] = (APP_PAGE_FINGERPRINT,),
    *,
    actions_override: tuple[AdapterActionSpec, ...] | None = None,
    additional_fields: tuple[AdapterFieldSpec, ...] = (),
) -> TransactionAdapterDescriptor:
    if actions_override is not None:
        return _adapter_with_actions(origin, fingerprints, actions_override)
    return TransactionAdapterDescriptor(
        extension_id=EXTENSION_ID,
        extension_version=EXTENSION_VERSION,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        display_name="在读证明申请",
        allowed_origins=(origin,),
        allowed_paths=(APP_PATH,),
        declared_risk=RiskLevel.EXTERNAL_WRITE,
        transaction_ids=(TRANSACTION_ID,),
        fields=(
            AdapterFieldSpec(
                field_id="reason",
                label="申请理由",
                kind="text",
                required=True,
                max_length=200,
                locator="ctl:0:0",
            ),
            AdapterFieldSpec(
                field_id="phone",
                label="联系电话",
                kind="text",
                required=True,
                max_length=20,
                locator="ctl:0:1",
            ),
            AdapterFieldSpec(
                field_id="delivery",
                label="领取方式",
                kind="select",
                required=True,
                locator="ctl:1:0",
            ),
        ) + additional_fields,
        actions=(
            AdapterActionSpec(
                action_id="proof.submit",
                locator="act:0",
                label="提交申请",
                kind="submit",
                risk=RiskLevel.EXTERNAL_WRITE,
                final=True,
                method="POST",
                target_origin=origin,
                target_path="/apps/proof/submit",
            ),
            AdapterActionSpec(
                action_id="proof.open",
                locator="act:0",
                label="在读证明申请",
                kind="navigate",
                risk=RiskLevel.INTERNAL_WRITE,
                transaction_id=TRANSACTION_ID,
                navigates_to_path=APP_PATH,
            ),
        ),
        login_paths=(LOGIN_PATH,),
        discovery_path=PORTAL_PATH,
        tracking_path=STATUS_PATH,
        receipt_locator="text:回执号",
        receipt_pattern=r"NJU-[0-9]{4}-[0-9]{4}",
        allowed_page_fingerprints=fingerprints,
        consequences="提交后进入院系审核，材料不实将影响办理。",
    )


def _adapter_with_actions(
    origin: str,
    fingerprints: tuple[str, ...],
    actions: tuple[AdapterActionSpec, ...],
) -> TransactionAdapterDescriptor:
    base = _adapter(origin, fingerprints)
    return TransactionAdapterDescriptor(
        extension_id=base.extension_id,
        extension_version=base.extension_version,
        adapter_id=base.adapter_id,
        adapter_version=base.adapter_version,
        display_name=base.display_name,
        allowed_origins=base.allowed_origins,
        allowed_paths=base.allowed_paths,
        declared_risk=base.declared_risk,
        transaction_ids=base.transaction_ids,
        fields=base.fields,
        actions=actions,
        login_paths=base.login_paths,
        discovery_path=base.discovery_path,
        tracking_path=base.tracking_path,
        receipt_locator=base.receipt_locator,
        receipt_pattern=base.receipt_pattern,
        allowed_page_fingerprints=base.allowed_page_fingerprints,
        consequences=base.consequences,
    )


@unittest.skipUnless(
    BROWSER_TESTS and PLAYWRIGHT_AVAILABLE,
    "set PA_TEST_BROWSER=1 and install the browser extra to run real-browser tests",
)
class BrowserCompanionRealTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="pa_f07_browser_")
        self.tls = generate_tls_pair("localhost")
        self.site = MockEhallSite(self.tls)
        self.site.start()
        self.companion = await start_companion()
        self.settings = Settings(
            environment="development",
            log_level="INFO",
            public_host="127.0.0.1",
            public_port=8000,
            admin_host="127.0.0.1",
            admin_port=8001,
            health_host="127.0.0.1",
            health_port=8010,
            storage_backend="memory",
            database_url="postgresql+asyncpg://assistant:change-me@127.0.0.1:5432/assistant",
            extension_root=Path(self.tmp) / "extensions",
            artifact_root=Path(self.tmp) / "artifacts",
            trust_cloudflare_access=False,
            public_origin=None,
            cf_access_team_domain=None,
            cf_access_aud=None,
            browser_companion_url=self.companion.url,
            browser_allowed_origins=(self.site.origin,),
            browser_origin_mode="allowlist",
            browser_submit_enabled=True,
        )
        self.client = LoopbackCompanionClient(
            base_url=self.companion.url, root_capability=self.companion.capability
        )
        self.container = build_container(self.settings, browser_companion=self.client)
        self.broker = self.container.browser_broker
        assert self.broker is not None
        self._sessions: list[str] = []

    async def asyncTearDown(self) -> None:
        import contextlib

        for session_id in self._sessions:
            with contextlib.suppress(Exception):
                await self.broker.cancel_session(session_id, extension_id=EXTENSION_ID)
        try:
            await self.container.aclose()
        finally:
            await self.companion.stop()
            self.site.stop()
            shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    async def new_session(
        self, *, fingerprints: tuple[str, ...] = (APP_PAGE_FINGERPRINT,)
    ) -> str:
        await self.broker.register_adapter(_adapter(self.site.origin, fingerprints))
        record = await self.broker.create_session(
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="办理在读证明",
        )
        self._sessions.append(record.session_id)
        return record.session_id

    async def navigate(self, session_id: str, url: str) -> object:
        return await self.broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            transaction_id=TRANSACTION_ID,
            url=url,
        )

    async def user_logs_in(self) -> None:
        """The human at the keyboard: fills the form and clicks login."""

        from playwright.async_api import async_playwright

        assert self.companion.cdp_port, "the companion is not in test mode"
        async with async_playwright() as playwright_api:
            cdp = await playwright_api.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.companion.cdp_port}"
            )
            try:
                context = cdp.contexts[0]
                page = next(
                    (item for item in context.pages if LOGIN_PATH in item.url), None
                )
                assert page is not None, "the login page is not open in the browser"
                await page.fill("#username", TEST_USERNAME)
                await page.fill("#password", TEST_PASSWORD)
                await page.click("#login-button")
                await page.wait_for_url(f"**{PORTAL_PATH}", timeout=15000)
            finally:
                await cdp.close()

    async def reach_authenticated(
        self, *, fingerprints: tuple[str, ...] = (APP_PAGE_FINGERPRINT,)
    ) -> str:
        session_id = await self.new_session(fingerprints=fingerprints)
        snapshot = await self.navigate(session_id, self.site.url(PORTAL_PATH))
        self.assertFalse(snapshot.authenticated)  # type: ignore[attr-defined]
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.WAITING_USER)
        await self.user_logs_in()
        snapshot = await self.navigate(session_id, self.site.url(PORTAL_PATH))
        self.assertTrue(snapshot.authenticated)  # type: ignore[attr-defined]
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.AUTHENTICATED)
        return session_id

    async def reach_prepared(
        self, query: str = "", *, fingerprints: tuple[str, ...] = (APP_PAGE_FINGERPRINT,)
    ) -> tuple[str, object]:
        session_id = await self.reach_authenticated(fingerprints=fingerprints)
        snapshot = await self.navigate(session_id, self.site.url(APP_PATH + query))
        await self.broker.record_discovery(
            session_id, extension_id=EXTENSION_ID, app_count=5
        )
        await self.broker.record_preparation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            app_id="proof",
            transaction_id=TRANSACTION_ID,
            page_fingerprint=snapshot.fingerprint,  # type: ignore[attr-defined]
            planned_fields=3,
        )
        return session_id, snapshot

    async def reach_prepared_with_successful_controls(
        self, *, consent_checked: bool
    ) -> tuple[str, object]:
        session_id = await self.reach_authenticated()
        query = "?successfulControls=1"
        if consent_checked:
            query += "&consentChecked=1"
        app_url = self.site.url(APP_PATH + query)

        # Read this deterministic local fixture once so the adapter can pin
        # the exact test-page version containing checkbox/radio/disabled
        # controls.  It is a GET against the local mock, before any fill/write.
        await self.client.navigate(session_id, app_url, login_paths=(LOGIN_PATH,))
        from personal_assistant.core.browser import (
            compute_page_fingerprint,
            page_structure_document,
        )

        raw = await self.client.snapshot(session_id, scan_text=True)
        structure = raw["structure"]
        observed_fingerprint = compute_page_fingerprint(
            page_structure_document(
                controls=structure.get("controls", ()),
                headings=structure.get("headings", ()),
                links=raw.get("links", ()),
                forms=structure.get("forms", ()),
                hidden_fields=structure.get("hidden_fields", ()),
            )
        )
        additional_fields = (
            AdapterFieldSpec(
                field_id="consent",
                label="同意办理",
                kind="checkbox",
                required=False,
                locator="ctl:0:2",
            ),
            AdapterFieldSpec(
                field_id="channel_email",
                label="电子领取",
                kind="radio",
                required=False,
                locator="ctl:0:3",
            ),
            AdapterFieldSpec(
                field_id="channel_paper",
                label="纸质领取",
                kind="radio",
                required=False,
                locator="ctl:0:4",
            ),
            AdapterFieldSpec(
                field_id="disabled_note",
                label="站点禁用备注",
                kind="text",
                required=False,
                locator="ctl:0:5",
            ),
        )
        await self.broker.register_adapter(
            _adapter(
                self.site.origin,
                (observed_fingerprint,),
                additional_fields=additional_fields,
            )
        )
        snapshot = await self.navigate(session_id, app_url)
        await self.broker.record_discovery(
            session_id, extension_id=EXTENSION_ID, app_count=5
        )
        await self.broker.record_preparation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            app_id="proof",
            transaction_id=TRANSACTION_ID,
            page_fingerprint=snapshot.fingerprint,  # type: ignore[attr-defined]
            planned_fields=7,
        )
        return session_id, snapshot

    def successful_controls_plan(
        self, snapshot: object, *, consent_value: str
    ) -> FillPlan:
        base = self.plan(snapshot)
        live = {item.field_id: item for item in snapshot.fields}  # type: ignore[attr-defined]
        extra = (
            FieldChange(
                "consent", "ctl:0:2", "同意办理", live["consent"].value,
                consent_value,
            ),
            FieldChange(
                "channel_email", "ctl:0:3", "电子领取",
                live["channel_email"].value, "false",
            ),
            FieldChange(
                "channel_paper", "ctl:0:4", "纸质领取",
                live["channel_paper"].value, "true",
            ),
        )
        return FillPlan(
            adapter_id=base.adapter_id,
            adapter_version=base.adapter_version,
            transaction_id=base.transaction_id,
            expected_origin=base.expected_origin,
            expected_page_fingerprint=base.expected_page_fingerprint,
            app_id=base.app_id,
            fields=(*base.fields, *extra),
            consequences=base.consequences,
            attachments=base.attachments,
        )

    def plan(
        self,
        snapshot: object,
        *,
        reason: str = "需要办理在读证明",
        phone: str = "13800000000",
        expected_fingerprint: str | None = None,
        attachments: tuple[AttachmentPreview, ...] = (),
    ) -> FillPlan:
        fields = {item.field_id: item for item in snapshot.fields}  # type: ignore[attr-defined]
        return FillPlan(
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            transaction_id=TRANSACTION_ID,
            expected_origin=self.site.origin,
            expected_page_fingerprint=expected_fingerprint or snapshot.fingerprint,  # type: ignore[attr-defined]
            app_id="proof",
            consequences="提交后进入院系审核。",
            attachments=attachments,
            fields=(
                FieldChange(
                    field_id="reason",
                    locator="ctl:0:0",
                    label="申请理由",
                    old_value=fields["reason"].value,
                    new_value=reason,
                    source=FieldValueSource.USER_INPUT,
                ),
                FieldChange(
                    field_id="phone",
                    locator="ctl:0:1",
                    label="联系电话",
                    old_value=fields["phone"].value,
                    new_value=phone,
                    source=FieldValueSource.USER_INPUT,
                ),
                FieldChange(
                    field_id="delivery",
                    locator="ctl:1:0",
                    label="领取方式",
                    old_value=fields["delivery"].value,
                    new_value=fields["delivery"].value,
                    source=FieldValueSource.USER_INPUT,
                ),
            ),
        )

    async def fill(self, session_id: str, plan: FillPlan) -> object:
        return await self.broker.execute_fill(
            session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
        )

    # -- tests -------------------------------------------------------------

    async def test_login_challenge_waits_for_the_user_in_a_headed_browser(self) -> None:
        session_id = await self.new_session()
        snapshot = await self.navigate(session_id, self.site.url(PORTAL_PATH))
        self.assertFalse(snapshot.authenticated)  # type: ignore[attr-defined]
        _, status = await self.broker.session_status(session_id, extension_id=EXTENSION_ID)
        self.assertFalse(status.headless)
        self.assertTrue(status.browser_alive)
        # The companion status must report the live page, not just booleans:
        # the acceptance harness and the broker rely on url/login_page.
        self.assertTrue(status.login_page)
        self.assertIn(LOGIN_PATH, status.url)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.WAITING_USER)
        self.assertEqual(self.site.submission_count(), 0)

    async def test_user_login_then_app_discovery_classifies_prohibited_apps(self) -> None:
        session_id = await self.reach_authenticated()
        snapshot, assessment = await self.broker.snapshot(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(snapshot.origin, self.site.origin)
        classified = await self.broker.classify_labels(
            ["在读证明申请", "退课申请", "在线缴费", "选课变更", "研究生成绩单打印"]
        )
        flagged = {label for label, matches in classified if matches}
        self.assertEqual(flagged, {"退课申请", "在线缴费", "选课变更"})
        self.assertEqual(assessment.risk, RiskLevel.READ)

    async def test_operator_can_capture_a_user_opened_popup_without_writes(self) -> None:
        session_id = await self.reach_authenticated()
        await self.client.navigate(session_id, self.site.url(POPUP_PORTAL_PATH))
        from playwright.async_api import async_playwright

        assert self.companion.cdp_port
        async with async_playwright() as playwright_api:
            cdp = await playwright_api.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.companion.cdp_port}"
            )
            try:
                page = next(
                    item for item in cdp.contexts[0].pages
                    if POPUP_PORTAL_PATH in item.url
                )
                async with page.expect_popup() as popup_event:
                    await page.click("#open-popup")
                popup = await popup_event.value
                await popup.wait_for_load_state("domcontentloaded")
            finally:
                await cdp.close()
        adopted = await self.client.adopt_opened_page(session_id)
        self.assertTrue(adopted["adopted"])
        snapshot = await self.client.snapshot(session_id, scan_text=False)
        self.assertIn(APP_PATH, snapshot["url"])
        self.assertEqual(0, self.site.submission_count())

    async def test_companion_selects_nested_transaction_frame(self) -> None:
        session_id = await self.reach_authenticated()
        await self.client.navigate(session_id, self.site.url(FRAME_SHELL_PATH))
        selected = await self.client.select_frame(
            session_id, origin=self.site.origin, path=APP_PATH
        )
        self.assertEqual(APP_PATH, selected["path"])
        snapshot = await self.client.snapshot(session_id, scan_text=False)
        self.assertEqual(self.site.url(APP_PATH), snapshot["url"])
        self.assertEqual("reason", snapshot["structure"]["controls"][0]["name"])
        self.assertEqual(0, self.site.submission_count())

    async def test_nested_frame_reaches_broker_preview_without_submit(self) -> None:
        session_id = await self.reach_authenticated()
        await self.client.navigate(session_id, self.site.url(FRAME_SHELL_PATH))
        await self.client.select_frame(
            session_id, origin=self.site.origin, path=APP_PATH
        )
        snapshot, assessment = await self.broker.snapshot(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            transaction_id=TRANSACTION_ID,
        )
        self.assertFalse(assessment.prohibited)
        await self.broker.record_discovery(
            session_id, extension_id=EXTENSION_ID, app_count=1
        )
        await self.broker.record_preparation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            app_id="proof",
            transaction_id=TRANSACTION_ID,
            page_fingerprint=snapshot.fingerprint,
            planned_fields=3,
        )
        preview = await self.fill(session_id, self.plan(snapshot))
        self.assertEqual("proof.submit", preview.target_action_id)
        self.assertEqual(0, self.site.submission_count())
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(BrowserSessionState.PREVIEW_READY, record.state)

    async def test_explicit_open_mode_reaches_another_https_origin(self) -> None:
        other_site = MockEhallSite(self.tls)
        other_site.start()
        open_session = "browser-open-mode"
        strict_session = "browser-allowlist-mode"
        try:
            await self.client.create_session(
                session_id=open_session,
                purpose="read-only open-origin test",
                allowed_origins=(self.site.origin,),
                origin_mode="open",
                task_id=TASK_ID,
                extension_id=EXTENSION_ID,
            )
            reached = await self.client.navigate(
                open_session, other_site.url(PORTAL_PATH)
            )
            self.assertEqual(other_site.origin, reached["origin"])
            status = await self.client.status(open_session)
            self.assertEqual(0, status["blocked_origin_requests"])
            self.assertEqual(0, other_site.submission_count())

            await self.client.create_session(
                session_id=strict_session,
                purpose="read-only allowlist test",
                allowed_origins=(self.site.origin,),
                origin_mode="allowlist",
                task_id=TASK_ID,
                extension_id=EXTENSION_ID,
            )
            with self.assertRaises(BrowserPolicyError):
                await self.client.navigate(
                    strict_session, other_site.url(PORTAL_PATH)
                )
        finally:
            await self.client.close_session(open_session)
            await self.client.close_session(strict_session)
            other_site.stop()

    async def test_fill_reaches_preview_ready_and_stops_before_submit(self) -> None:
        session_id, snapshot = await self.reach_prepared()
        preview = await self.fill(session_id, self.plan(snapshot))
        self.assertEqual(preview.session_id, session_id)  # type: ignore[attr-defined]
        self.assertEqual(preview.origin, self.site.origin)  # type: ignore[attr-defined]
        values = {item.field_id: item.new_value for item in preview.fields}  # type: ignore[attr-defined]
        self.assertEqual(values["reason"], "需要办理在读证明")
        self.assertEqual(values["phone"], "13800000000")
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.PREVIEW_READY)
        self.assertEqual(self.site.submission_count(), 0)

    async def test_fill_cannot_trigger_a_page_autosave(self) -> None:
        session_id, snapshot = await self.reach_prepared(query="?autosave=1")
        await self.fill(session_id, self.plan(snapshot))
        self.assertEqual(self.site.autosave_attempts(), 0)
        _, status = await self.broker.session_status(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(status.fill_operations, 1)

    async def test_delayed_autosave_stays_blocked_after_fill_and_reads(self) -> None:
        for delay in (500, 1500, 2500):
            with self.subTest(delay=delay):
                session_id, snapshot = await self.reach_prepared(
                    query=f"?autosave=1&delay={delay}"
                )
                await self.fill(session_id, self.plan(snapshot))
                # Reading the page after the fill must not lift the block.
                await self.broker.snapshot(session_id, extension_id=EXTENSION_ID)
                await self.broker.find_text(
                    session_id, extension_id=EXTENSION_ID, query="回执号"
                )
                await self._sleep((delay + 1200) / 1000)
                self.assertEqual(self.site.autosave_attempts(), 0)
                _, status = await self.broker.session_status(
                    session_id, extension_id=EXTENSION_ID
                )
                self.assertGreaterEqual(status.blocked_mutating_requests, 1)
                driver = await self._driver_diagnostics(session_id)
                self.assertTrue(
                    any(
                        item["method"] == "POST"
                        for item in driver["blocked_request_samples"]
                    )
                )
                self.assertNotIn("?", str(driver["blocked_request_samples"]))
                await self.broker.cancel_session(session_id, extension_id=EXTENSION_ID)
                self._sessions.remove(session_id)

    async def test_open_transaction_routes_from_the_portal_and_verifies_the_landing(self) -> None:
        session_id = await self.reach_authenticated()
        result = await self.broker.execute_navigation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            transaction_id=TRANSACTION_ID,
        )
        self.assertEqual(result["path"], APP_PATH)
        self.assertEqual(result["page_fingerprint"], APP_PAGE_FINGERPRINT)
        self.assertEqual(result["state"], BrowserSessionState.AUTHENTICATED.value)
        self.assertEqual(self.site.submission_count(), 0)
        # The bound landing is now the transaction page: a fill can follow.
        snapshot, _ = await self.broker.snapshot(
            session_id, extension_id=EXTENSION_ID, adapter_id=ADAPTER_ID
        )
        self.assertEqual(snapshot.fingerprint, APP_PAGE_FINGERPRINT)  # type: ignore[attr-defined]

    async def test_open_transaction_rejects_a_mismatched_landing_path(self) -> None:
        session_id = await self.reach_authenticated()
        await self.broker.register_adapter(
            _adapter(
                self.site.origin,
                actions_override=(
                    AdapterActionSpec(
                        action_id="proof.open",
                        locator="act:0",
                        label="在读证明申请",
                        kind="navigate",
                        risk=RiskLevel.INTERNAL_WRITE,
                        transaction_id=TRANSACTION_ID,
                        navigates_to_path="/apps/transcript",
                    ),
                ),
            )
        )
        with self.assertRaises(Exception) as caught:
            await self.broker.execute_navigation(
                session_id,
                extension_id=EXTENSION_ID,
                adapter_id=ADAPTER_ID,
                transaction_id=TRANSACTION_ID,
            )
        self.assertEqual(
            getattr(caught.exception, "reason", ""), "BROWSER_NAVIGATION_MISMATCH"
        )
        self.assertEqual(self.site.submission_count(), 0)

    async def test_live_page_matches_the_pinned_fingerprint(self) -> None:
        session_id, snapshot = await self.reach_prepared()
        self.assertEqual(snapshot.fingerprint, APP_PAGE_FINGERPRINT)  # type: ignore[attr-defined]
        self.assertEqual(self.site.submission_count(), 0)

    async def test_unknown_page_version_is_refused_for_the_note_variant(self) -> None:
        session_id = await self.reach_authenticated()
        with self.assertRaises(Exception) as caught:
            await self.navigate(session_id, self.site.url(APP_PATH + "?note=1"))
        self.assertEqual(getattr(caught.exception, "reason", ""), "UNKNOWN_PAGE_VERSION")
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(record.fill_count, 0)

    async def test_unknown_page_version_is_refused_for_the_extra_field_variant(self) -> None:
        session_id = await self.reach_authenticated()
        with self.assertRaises(Exception) as caught:
            await self.navigate(session_id, self.site.url(APP_PATH + "?extra=1"))
        self.assertEqual(getattr(caught.exception, "reason", ""), "UNKNOWN_PAGE_VERSION")
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(self.site.submission_count(), 0)

    async def test_a_pinned_variant_still_blocks_unknown_fields(self) -> None:
        # Register the variant fingerprint deliberately: the page version is
        # then known, and the unknown-field guard must fail closed instead.
        from personal_assistant.core.browser import (
            compute_page_fingerprint,
            page_structure_document,
        )

        session_id = await self.reach_authenticated()
        # Observe the variant page through the raw port (no policy), then pin
        # its fingerprint explicitly so the page version is known.
        await self.client.navigate(session_id, self.site.url(APP_PATH + "?extra=1"))
        raw = await self.client.snapshot(session_id, scan_text=True)
        structure = raw["structure"]
        variant = compute_page_fingerprint(
            page_structure_document(
                controls=structure.get("controls", ()),
                headings=structure.get("headings", ()),
                links=raw.get("links", ()),
                forms=structure.get("forms", ()),
            )
        )
        self.assertNotEqual(variant, APP_PAGE_FINGERPRINT)
        await self.broker.register_adapter(
            _adapter(self.site.origin, (APP_PAGE_FINGERPRINT, variant))
        )
        snapshot = await self.navigate(session_id, self.site.url(APP_PATH + "?extra=1"))
        await self.broker.record_discovery(
            session_id, extension_id=EXTENSION_ID, app_count=5
        )
        await self.broker.record_preparation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            app_id="proof",
            transaction_id=TRANSACTION_ID,
            page_fingerprint=snapshot.fingerprint,  # type: ignore[attr-defined]
            planned_fields=3,
        )
        with self.assertRaises(Exception) as caught:
            await self.fill(session_id, self.plan(snapshot))
        self.assertEqual(getattr(caught.exception, "reason", ""), "UNKNOWN_FIELD")
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(record.fill_count, 0)
        self.assertEqual(self.site.submission_count(), 0)

    async def test_incomplete_text_scan_pauses_the_session(self) -> None:
        session_id = await self.reach_authenticated()
        with self.assertRaises(Exception) as caught:
            await self.navigate(session_id, self.site.url(APP_PATH + "?huge=1"))
        self.assertEqual(
            getattr(caught.exception, "reason", ""), "PAGE_TEXT_SCAN_INCOMPLETE"
        )
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(self.site.submission_count(), 0)

    async def test_prohibited_page_text_blocks_fill_with_zero_page_writes(self) -> None:
        session_id, snapshot = await self.reach_prepared(query="?risk=1")
        with self.assertRaises(Exception) as caught:
            await self.fill(session_id, self.plan(snapshot))
        self.assertIn("PROHIBITED", str(caught.exception))
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(record.fill_count, 0)

    async def test_off_allowlist_origins_never_reach_the_network(self) -> None:
        session_id = await self.reach_authenticated()
        with self.assertRaises(NavigationDeniedError):
            await self.navigate(session_id, f"https://127.0.0.1:{self.site.port}/portal")
        # An off-allowlist subresource on an allowed page is aborted and counted.
        await self.navigate(session_id, self.site.url(APP_PATH + "?external=1"))
        _, status = await self.broker.session_status(session_id, extension_id=EXTENSION_ID)
        self.assertGreaterEqual(status.blocked_origin_requests, 1)

    async def test_login_window_allows_only_the_frozen_authentication_post(self) -> None:
        session_id = await self.new_session()
        await self.navigate(session_id, self.site.url(PORTAL_PATH))
        _, status = await self.broker.session_status(session_id, extension_id=EXTENSION_ID)
        self.assertTrue(status.login_page)
        from playwright.async_api import async_playwright

        async with async_playwright() as api:
            cdp = await api.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.companion.cdp_port}"
            )
            try:
                page = next(
                    item for item in cdp.contexts[0].pages if LOGIN_PATH in item.url
                )
                # Background fetch/XHR is never a login submission.
                await page.evaluate(
                    "fetch('/apps/proof/autosave',{method:'POST',body:'x'}).catch(()=>{})"
                )
                # A subframe form POST to another path is not the frozen submit.
                await page.evaluate(
                    "const f=document.createElement('iframe');document.body.appendChild(f);"
                    "const d=f.contentDocument;const form=d.createElement('form');"
                    "form.method='post';form.action='/apps/withdraw';d.body.appendChild(form);"
                    "form.submit();"
                )
                await self._sleep(1.2)
            finally:
                await cdp.close()
        driver = await self._driver_diagnostics(session_id)
        self.assertGreaterEqual(driver["blocked_mutating_requests"], 2)
        self.assertEqual(0, driver["user_auth_requests"])
        self.assertEqual(["POST", self.site.origin, LOGIN_PATH], driver["login_target"])
        # The human login still works and consumes exactly one frozen POST.
        await self.user_logs_in()
        await self.navigate(session_id, self.site.url(PORTAL_PATH))
        driver = await self._driver_diagnostics(session_id)
        self.assertEqual(1, driver["user_auth_requests"])
        # After the challenge the window is closed: a main-frame POST to
        # another path is blocked again.
        async with async_playwright() as api:
            cdp = await api.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.companion.cdp_port}"
            )
            try:
                page = next(
                    item
                    for item in cdp.contexts[0].pages
                    if PORTAL_PATH in item.url
                )
                await page.evaluate(
                    "const f=document.createElement('form');f.method='post';"
                    "f.action='/apps/withdraw';document.body.appendChild(f);f.submit();"
                )
                await self._sleep(1.0)
            finally:
                await cdp.close()
        driver = await self._driver_diagnostics(session_id)
        self.assertGreaterEqual(driver["blocked_mutating_requests"], 3)

    async def _driver_diagnostics(self, session_id: str) -> Mapping[str, Any]:
        diagnostics = await self.client.diagnostics()
        sessions = diagnostics["sessions"]
        return next(item for item in sessions if item["session_id"] == session_id)[
            "driver"
        ]

    async def test_authentication_secrets_never_leave_the_browser(self) -> None:
        session_id = await self.reach_authenticated()
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright_api:
            cdp = await playwright_api.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.companion.cdp_port}"
            )
            try:
                context = cdp.contexts[0]
                page = context.pages[0]
                cdp_session = await context.new_cdp_session(page)
                cookies = (await cdp_session.send("Network.getAllCookies"))["cookies"]
            finally:
                await cdp.close()
        token = next(
            (item["value"] for item in cookies if item["name"] == "PA_SESSION"), ""
        )
        self.assertTrue(token)
        snapshot, _ = await self.broker.snapshot(session_id, extension_id=EXTENSION_ID)
        host_view = await self.container.browser_host_capability.handle(  # type: ignore[union-attr]
            HOST_BROWSER_SNAPSHOT,
            {"session_id": session_id},
            context=BrowserCapabilityContext(
                extension_id=EXTENSION_ID, extension_version=EXTENSION_VERSION
            ),
        )
        records = await self.container.browser_sessions.find_active_for_task(TASK_ID)
        blobs = [
            json.dumps(host_view, default=str),
            "".join(repr(asdict(record)) for record in records),
            repr(self.settings),
            repr(snapshot),
            json.dumps(await self.client.diagnostics(), default=str),
        ]
        for root, _dirs, files in os.walk(self.tmp):
            for name in files:
                path = Path(root) / name
                blobs.append(path.read_text("utf-8", "replace"))
        combined = "\n".join(blobs)
        self.assertNotIn(token, combined)
        self.assertNotIn(TEST_PASSWORD, combined)
        self.assertNotIn("password=", combined)

    async def test_cancel_reaps_the_browser_process(self) -> None:
        session_id = await self.reach_authenticated()
        diagnostics = await self.client.diagnostics()
        session_diag = next(
            item
            for item in diagnostics["sessions"]
            if item["session_id"] == session_id
        )
        browser_pid = session_diag["driver"].get("browser_process_id")
        self.assertTrue(browser_pid)
        await self.broker.cancel_session(session_id, extension_id=EXTENSION_ID)
        self._sessions.remove(session_id)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.CANCELLED)
        for _ in range(40):
            if not pid_alive(int(browser_pid)):
                break
            await self._sleep(0.25)
        self.assertFalse(pid_alive(int(browser_pid)), "the browser process survived cancel")
        # Repeated cancellation stays safe.
        await self.broker.cancel_session(session_id, extension_id=EXTENSION_ID)

    async def test_repeated_cancel_and_companion_restart_are_safe(self) -> None:
        session_id = await self.new_session()
        await self.broker.cancel_session(session_id, extension_id=EXTENSION_ID)
        await self.broker.cancel_session(session_id, extension_id=EXTENSION_ID)
        self._sessions.clear()
        await self.companion.stop()
        # The old capability must no longer work after a companion restart.
        self.companion = await start_companion()
        with self.assertRaises(BrowserUnavailableError):
            await self.client.status("brs_exec")

    async def test_submit_requires_the_enabled_gate_and_stops_before_click(self) -> None:
        session_id, snapshot = await self.reach_prepared()
        preview = await self.fill(session_id, self.plan(snapshot))
        disabled_broker = type(self.broker)(
            companion=self.client,
            sessions=self.container.browser_sessions,
            adapters=self.container.browser_adapters,
            allowed_origins=frozenset({self.site.origin}),
            submit_enabled=False,
        )
        with self.assertRaises(Exception) as caught:
            await disabled_broker.execute_submit(
                session_id,
                task_id=TASK_ID,
                extension_id=EXTENSION_ID,
                preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
                preview_nonce=preview.nonce,  # type: ignore[attr-defined]
                action_id="proof.submit",
            )
        self.assertEqual(getattr(caught.exception, "reason", ""), "SUBMIT_DISABLED")
        self.assertEqual(self.site.submission_count(), 0)

    async def test_lost_submit_response_converges_via_host_tracking(self) -> None:
        session_id, snapshot = await self.reach_prepared()
        self.site.set_lose_response(True)
        preview = await self.fill(session_id, self.plan(snapshot))
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        # The lost HTTP response is resolved by the immediate read-only
        # tracking diff, never by the (possibly fabricated) DOM.
        self.assertEqual(outcome.state, BrowserSessionState.SUCCEEDED)
        self.assertIn("NJU-2026-", outcome.reference)
        self.assertEqual(self.site.submission_count(), 1)
        _, status = await self.broker.session_status(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(status.click_operations, 1)

    async def test_successful_controls_use_browser_checkbox_radio_and_disabled_semantics(
        self,
    ) -> None:
        session_id, snapshot = await self.reach_prepared_with_successful_controls(
            consent_checked=True
        )
        preview = await self.fill(
            session_id,
            self.successful_controls_plan(snapshot, consent_value="true"),
        )
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )

        self.assertEqual(outcome.state, BrowserSessionState.SUCCEEDED)
        self.assertEqual(self.site.submission_count(), 1)
        submitted = self.site.state.submissions[-1]
        self.assertEqual(submitted["consent"], "accepted")
        self.assertEqual(submitted["channel"], "paper")
        self.assertEqual(submitted["disabled_note"], "")

    async def test_unchecked_checkbox_is_omitted_from_successful_controls(self) -> None:
        session_id, snapshot = await self.reach_prepared_with_successful_controls(
            consent_checked=False
        )
        preview = await self.fill(
            session_id,
            self.successful_controls_plan(snapshot, consent_value="false"),
        )
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )

        self.assertEqual(outcome.state, BrowserSessionState.SUCCEEDED)
        self.assertEqual(self.site.submission_count(), 1)
        submitted = self.site.state.submissions[-1]
        self.assertEqual(submitted["consent"], "")
        self.assertEqual(submitted["channel"], "paper")
        self.assertEqual(submitted["disabled_note"], "")

    async def test_a_lost_submit_without_tracking_evidence_stays_unknown(self) -> None:
        session_id, snapshot = await self.reach_prepared()
        self.site.set_lose_response(True)
        self.site.set_hide_tracking(True)
        preview = await self.fill(session_id, self.plan(snapshot))
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        self.assertEqual(outcome.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(outcome.diagnostic_code, "RECONCILE_NOT_FOUND")
        with self.assertRaises(BrowserSessionStateError):
            await self.broker.execute_submit(
                session_id,
                task_id=TASK_ID,
                extension_id=EXTENSION_ID,
                preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
                preview_nonce=preview.nonce,  # type: ignore[attr-defined]
                action_id="proof.submit",
            )
        _, status = await self.broker.session_status(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(status.click_operations, 1)

    async def test_fake_dom_receipt_without_a_real_post_stays_unknown(self) -> None:
        session_id, snapshot = await self.reach_prepared(query="?double=1&fakeReceipt=1")
        preview = await self.fill(session_id, self.plan(snapshot))
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        # The background fetch may not consume the submit allowance and the
        # page script prevents the real form navigation, so no write happens:
        # a fabricated DOM receipt (NJU-2026-9999) must never become success.
        self.assertEqual(outcome.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(self.site.submission_count(), 0)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertIsNone(record.receipt)

    async def test_a_rewritten_form_action_is_never_written(self) -> None:
        session_id, snapshot = await self.reach_prepared(query="?swap=1")
        preview = await self.fill(session_id, self.plan(snapshot))
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        self.assertEqual(outcome.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(self.site.submission_count(), 0)
        _, status = await self.broker.session_status(session_id, extension_id=EXTENSION_ID)
        self.assertGreaterEqual(status.blocked_mutating_requests, 1)

    async def test_a_load_time_action_rewrite_is_refused_as_unknown_version(self) -> None:
        session_id = await self.reach_authenticated()
        with self.assertRaises(Exception) as caught:
            await self.navigate(session_id, self.site.url(APP_PATH + "?swapLoad=1"))
        self.assertEqual(getattr(caught.exception, "reason", ""), "UNKNOWN_PAGE_VERSION")
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.SAFETY_PAUSED)
        self.assertEqual(self.site.submission_count(), 0)

    async def test_a_second_concurrent_write_is_aborted(self) -> None:
        session_id, snapshot = await self.reach_prepared(query="?double=1")
        preview = await self.fill(session_id, self.plan(snapshot))
        await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        self.assertEqual(self.site.submission_count(), 1)
        _, status = await self.broker.session_status(session_id, extension_id=EXTENSION_ID)
        self.assertGreaterEqual(status.blocked_mutating_requests, 1)

    async def test_receipt_baseline_excludes_historical_receipts(self) -> None:
        # A historical receipt lives on the tracking page, not on the form.
        self.site.seed_receipt("NJU-2026-0001")
        session_id, snapshot = await self.reach_prepared()
        self.site.set_lose_response(True)
        preview = await self.fill(session_id, self.plan(snapshot))
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        self.assertEqual(outcome.state, BrowserSessionState.SUCCEEDED)
        self.assertEqual(self.site.submission_count(), 2)
        record = await self.broker.get_session(session_id, extension_id=EXTENSION_ID)
        self.assertIn('"captured":true', record.receipt_baseline)
        # Only hashes are stored, and the historical reference is excluded.
        self.assertNotIn("NJU-2026-0001", record.receipt_baseline)
        assert record.receipt is not None
        self.assertEqual(record.receipt["reference"], "NJU-2026-0002")
        self.assertEqual(record.receipt["issued_by"], "host_tracking")

    async def test_historical_receipt_never_matches_without_a_submission(self) -> None:
        # The only receipt on the tracking page is a historical one; a DOM-only
        # fake submission must never be reconciled into SUCCEEDED.
        self.site.seed_receipt("NJU-2026-0001")
        session_id, snapshot = await self.reach_prepared(query="?fakeReceipt=1")
        preview = await self.fill(session_id, self.plan(snapshot))
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        self.assertEqual(outcome.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(self.site.submission_count(), 1)
        record = await self.broker.reconcile(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(record.diagnostic_code, "RECONCILE_NOT_FOUND")

    async def test_dom_only_receipt_is_unknown_and_never_succeeds(self) -> None:
        session_id, snapshot = await self.reach_prepared(query="?fakeReceipt=1")
        preview = await self.fill(session_id, self.plan(snapshot))
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        self.assertEqual(outcome.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(self.site.submission_count(), 0)

    async def test_an_injected_hidden_field_aborts_the_write(self) -> None:
        session_id, snapshot = await self.reach_prepared(query="?payloadDrift=1")
        preview = await self.fill(session_id, self.plan(snapshot))
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        # The approved payload was rewritten at submit time: the bound write is
        # aborted, nothing reaches the server and the result stays UNKNOWN.
        self.assertEqual(outcome.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(self.site.submission_count(), 0)
        driver = await self._driver_diagnostics(session_id)
        self.assertGreaterEqual(driver["payload_mismatches"], 1)

    async def test_a_tracking_redirect_aborts_the_submit_before_the_click(self) -> None:
        session_id, snapshot = await self.reach_prepared()
        self.site.set_tracking_redirect(True)
        preview = await self.fill(session_id, self.plan(snapshot))
        with self.assertRaises(Exception) as caught:
            await self.broker.execute_submit(
                session_id,
                task_id=TASK_ID,
                extension_id=EXTENSION_ID,
                preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
                preview_nonce=preview.nonce,  # type: ignore[attr-defined]
                action_id="proof.submit",
            )
        self.assertEqual(
            getattr(caught.exception, "reason", ""), "TRACKING_URL_MISMATCH"
        )
        self.assertEqual(self.site.submission_count(), 0)
        _, status = await self.broker.session_status(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(status.click_operations, 0)

    async def test_reconcile_never_mints_a_proof_from_a_redirected_page(self) -> None:
        session_id, snapshot = await self.reach_prepared(query="?fakeReceipt=1")
        preview = await self.fill(session_id, self.plan(snapshot))
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        self.assertEqual(outcome.state, BrowserSessionState.UNKNOWN)
        self.site.set_tracking_redirect(True)
        record = await self.broker.reconcile(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(record.diagnostic_code, "RECONCILE_UNSAFE")
        self.assertIsNone(record.receipt)

    async def test_companion_is_reachable_immediately_after_its_handshake(self) -> None:
        for _ in range(3):
            process = await start_companion()
            client = LoopbackCompanionClient(
                base_url=process.url, root_capability=process.capability
            )
            try:
                diagnostics = await client.diagnostics()
                self.assertEqual(0, diagnostics["session_count"])
            finally:
                await client.aclose()
                await process.stop()

    async def test_static_hidden_token_is_submittable_and_never_exported(self) -> None:
        from personal_assistant.core.browser import (
            compute_page_fingerprint,
            page_structure_document,
        )

        session_id = await self.reach_authenticated()
        # Pin the variant page that carries a CSRF-style hidden token.
        await self.client.navigate(session_id, self.site.url(APP_PATH + "?staticToken=1"))
        raw = await self.client.snapshot(session_id, scan_text=True)
        structure = raw["structure"]
        variant = compute_page_fingerprint(
            page_structure_document(
                controls=structure.get("controls", ()),
                headings=structure.get("headings", ()),
                links=raw.get("links", ()),
                forms=structure.get("forms", ()),
                hidden_fields=structure.get("hidden_fields", ()),
            )
        )
        hidden = [
            item
            for item in structure.get("hidden_fields", [])
            if item.get("name") == "csrf"
        ]
        self.assertEqual(1, len(hidden))
        # Hidden inputs are pinned by name/type only; the token value stays
        # inside the browser process and never appears in the snapshot.
        self.assertNotIn("value", hidden[0])
        import json as _json

        self.assertNotIn("static-token-1", _json.dumps(raw, ensure_ascii=False))
        await self.broker.register_adapter(
            _adapter(self.site.origin, (APP_PAGE_FINGERPRINT, variant))
        )
        snapshot = await self.navigate(session_id, self.site.url(APP_PATH + "?staticToken=1"))
        await self.broker.record_discovery(
            session_id, extension_id=EXTENSION_ID, app_count=5
        )
        await self.broker.record_preparation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            app_id="proof",
            transaction_id=TRANSACTION_ID,
            page_fingerprint=snapshot.fingerprint,  # type: ignore[attr-defined]
            planned_fields=3,
        )
        preview = await self.fill(session_id, self.plan(snapshot))
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        # The static hidden field (never approved) does not break the bound
        # write, and success still comes from the host tracking proof.
        self.assertEqual(outcome.state, BrowserSessionState.SUCCEEDED)
        self.assertEqual(self.site.submission_count(), 1)
        self.assertTrue(
            all(
                field.kind not in {"hidden", "password"}
                for field in snapshot.fields  # type: ignore[attr-defined]
            )
        )

    async def test_a_tracking_query_redirect_never_mints_a_proof(self) -> None:
        session_id, snapshot = await self.reach_prepared(query="?fakeReceipt=1")
        preview = await self.fill(session_id, self.plan(snapshot))
        outcome = await self.broker.execute_submit(
            session_id,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            preview_hash=preview.canonical_payload_hash,  # type: ignore[attr-defined]
            preview_nonce=preview.nonce,  # type: ignore[attr-defined]
            action_id="proof.submit",
        )
        self.assertEqual(outcome.state, BrowserSessionState.UNKNOWN)
        self.site.set_tracking_query_redirect(True)
        record = await self.broker.reconcile(session_id, extension_id=EXTENSION_ID)
        self.assertEqual(record.state, BrowserSessionState.UNKNOWN)
        self.assertEqual(record.diagnostic_code, "RECONCILE_UNSAFE")
        self.assertIsNone(record.receipt)

    async def test_tracking_collection_is_bounded_and_host_side(self) -> None:
        self.site.seed_receipt("NJU-2026-0001")
        session_id = await self.reach_authenticated()
        await self.client.navigate(session_id, self.site.url(STATUS_PATH))
        collected = await self.client.collect_matches(
            session_id, pattern=r"NJU-[0-9]{4}-[0-9]{4}", limit=16
        )
        self.assertIn("NJU-2026-0001", collected["matches"])
        self.assertFalse(collected["truncated"])

    @staticmethod
    async def _sleep(seconds: float) -> None:
        import asyncio

        await asyncio.sleep(seconds)


if __name__ == "__main__":
    unittest.main()
