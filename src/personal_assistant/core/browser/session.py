"""The supervised browser session broker.

The broker is vendor-neutral orchestration: it owns the session state machine,
the origin/risk policy, fingerprint drift detection, field plan verification and
the authoritative structured preview.  Playwright never appears here; the actual
browser work happens in a Desktop Companion behind :class:`DesktopBrowserPort`.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from personal_assistant.core.approvals.canonicalize import canonical_json
from personal_assistant.core.browser.errors import (
    BrowserError,
    BrowserLimitError,
    BrowserPolicyError,
    BrowserSessionStateError,
    NavigationDeniedError,
    PageDriftError,
    PreviewExpiredError,
    ProhibitedTransactionError,
)
from personal_assistant.core.browser.fingerprint import (
    compute_page_fingerprint,
    page_structure_document,
)
from personal_assistant.core.browser.models import (
    MAX_ACTIONS,
    AttachmentPreview,
    BrowserLimits,
    BrowserSessionState,
    FieldChange,
    FieldValidation,
    FieldValueSource,
    FillPlan,
    PageAction,
    PageField,
    PageLink,
    PageSnapshot,
    RiskSignal,
    SubmissionOutcome,
    TransactionPreview,
    allowed_browser_transitions,
    canonical_preview_sha256,
    ensure_browser_transition,
)
from personal_assistant.core.browser.policy import (
    ProhibitedCategory,
    ProhibitedMatch,
    RiskAssessment,
    assess_risk,
    evaluate_navigation,
    risk_rank,
    scan_text_for_prohibited_terms,
)
from personal_assistant.core.browser.ports import (
    AdapterActionSpec,
    AdapterFieldSpec,
    BrowserAdapterRecord,
    BrowserAdapterStore,
    BrowserSessionRecord,
    BrowserSessionStore,
    CompanionSessionStatus,
    DesktopBrowserPort,
    TransactionAdapterDescriptor,
    validate_adapter_descriptor,
)
from personal_assistant.domain.enums import RiskLevel
from personal_assistant.domain.errors import AlreadyExistsError

SESSION_TTL = timedelta(minutes=30)
_PREVIEW_TTL = timedelta(seconds=300)
_TERMINAL_STATES = frozenset(
    {
        BrowserSessionState.SUCCEEDED,
        BrowserSessionState.FAILED,
        BrowserSessionState.CANCELLED,
    }
)
_ACTIVE_STATES = frozenset(
    state for state in BrowserSessionState if state not in _TERMINAL_STATES
)
# UNKNOWN is a pending external action: it occupies the task slot until a
# read-only reconciliation or a human decision resolves it, so the browser TTL
# must never turn it into a terminal CANCELLED (which the state machine forbids).
# EXECUTING is likewise excluded: the state machine only allows
# EXECUTING -> {SUCCEEDED, FAILED, UNKNOWN}, and startup recovery owns crashed
# executions, so a TTL expiry must not raise mid-transition.
_EXPIRABLE_STATES = frozenset(
    _ACTIVE_STATES - {BrowserSessionState.UNKNOWN, BrowserSessionState.EXECUTING}
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _default_id() -> str:
    return f"brs_{secrets.token_urlsafe(18)}"


def _require_mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BrowserPolicyError("COMPANION_MALFORMED", f"companion {what} is malformed")
    return value


def _require_str(value: Any, what: str, *, max_length: int = 4096) -> str:
    if not isinstance(value, str) or len(value) > max_length:
        raise BrowserPolicyError("COMPANION_MALFORMED", f"companion {what} is malformed")
    return value


class BrowserSessionBroker:
    def __init__(
        self,
        *,
        companion: DesktopBrowserPort,
        sessions: BrowserSessionStore,
        adapters: BrowserAdapterStore,
        allowed_origins: frozenset[str] | set[str],
        limits: BrowserLimits | None = None,
        submit_enabled: bool = False,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        self._companion = companion
        self._sessions = sessions
        self._adapters = adapters
        self._allowed_origins = frozenset(allowed_origins)
        self._limits = limits or BrowserLimits()
        self._submit_enabled = submit_enabled
        self._now = now or _utcnow
        self._id_factory = id_factory or _default_id
        self._nonce_factory = nonce_factory or (lambda: secrets.token_urlsafe(32))

    @property
    def allowed_origins(self) -> frozenset[str]:
        return self._allowed_origins

    @property
    def limits(self) -> BrowserLimits:
        return self._limits

    @property
    def submit_enabled(self) -> bool:
        return self._submit_enabled

    # ------------------------------------------------------------------ setup

    def _require_origins(self) -> None:
        if not self._allowed_origins:
            raise BrowserPolicyError(
                "NO_ALLOWED_ORIGINS",
                "no browser origin is allow-listed; refusing to open a session",
            )

    async def create_session(
        self,
        *,
        task_id: str,
        extension_id: str,
        extension_version: str,
        purpose: str,
        owner_id: str = "",
    ) -> BrowserSessionRecord:
        self._require_origins()
        if not task_id or not extension_id:
            raise BrowserPolicyError("INVALID_SESSION_REQUEST", "task and extension are required")
        if not purpose or len(purpose) > 500:
            raise BrowserPolicyError("INVALID_SESSION_REQUEST", "a purpose is required")
        now = self._now()
        existing: list[BrowserSessionRecord] = []
        for candidate in await self._sessions.find_active_for_task(task_id):
            if candidate.state not in _ACTIVE_STATES:
                continue
            if candidate.state in _EXPIRABLE_STATES and _is_expired(candidate, now):
                await self._expire(candidate, "SESSION_EXPIRED")
                continue
            existing.append(candidate)
        if existing:
            record = existing[0]
            if record.extension_id != extension_id:
                raise BrowserLimitError("a task already owns an active browser session")
            return record
        session_id = self._id_factory()
        record = BrowserSessionRecord(
            session_id=session_id,
            task_id=task_id,
            extension_id=extension_id,
            extension_version=extension_version,
            purpose=purpose,
            state=BrowserSessionState.REQUESTED,
            created_at=now,
            updated_at=now,
            expires_at=now + SESSION_TTL,
            owner_id=owner_id,
        )
        try:
            created = await self._sessions.create(record)
        except AlreadyExistsError:
            # A concurrent creator won the single-active-session constraint.
            for candidate in await self._sessions.find_active_for_task(task_id):
                if candidate.state in _ACTIVE_STATES and not _is_expired(candidate, now):
                    if candidate.extension_id != extension_id:
                        raise BrowserLimitError(
                            "a task already owns an active browser session"
                        ) from None
                    return candidate
            raise
        try:
            payload = await self._companion.create_session(
                session_id=session_id,
                purpose=purpose,
                allowed_origins=tuple(sorted(self._allowed_origins)),
                task_id=task_id,
                extension_id=extension_id,
            )
        except BaseException:
            await self._sessions.save(
                _replace_state(created, BrowserSessionState.CANCELLED, self._now()),
                expected_version=created.version,
            )
            raise
        url = _require_str(payload.get("url", ""), "session url", max_length=2048)
        origin = _require_str(payload.get("origin", ""), "session origin", max_length=255)
        if url and url != "about:blank":
            evaluate_navigation(url, allowed_origins=self._allowed_origins)
        return await self._save(created, {"url": url, "origin": origin})

    async def register_adapter(
        self, descriptor: TransactionAdapterDescriptor
    ) -> BrowserAdapterRecord:
        validate_adapter_descriptor(descriptor, allowed_origins=self._allowed_origins)
        document = _descriptor_document(descriptor)
        now = self._now()
        existing = await self._adapters.get(
            descriptor.extension_id, descriptor.adapter_id, descriptor.adapter_version
        )
        record = BrowserAdapterRecord(
            extension_id=descriptor.extension_id,
            extension_version=descriptor.extension_version,
            adapter_id=descriptor.adapter_id,
            adapter_version=descriptor.adapter_version,
            descriptor=document,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            version=(existing.version + 1) if existing else 0,
        )
        return await self._adapters.upsert(record)

    async def list_adapters(self, extension_id: str) -> tuple[BrowserAdapterRecord, ...]:
        return await self._adapters.list_for_extension(extension_id)

    async def classify_labels(
        self, labels: Sequence[str], *, forbidden_terms: Sequence[str] = ()
    ) -> tuple[tuple[str, tuple[ProhibitedMatch, ...]], ...]:
        if len(labels) > 64:
            raise BrowserLimitError("too many labels to classify")
        cleaned = [item[:500] for item in labels if isinstance(item, str)]
        return tuple(
            (label, scan_text_for_prohibited_terms(label, forbidden_terms)) for label in cleaned
        )

    # ------------------------------------------------------------------ session

    async def get_session(self, session_id: str, *, extension_id: str) -> BrowserSessionRecord:
        record = await self._sessions.get(session_id)
        if record is None:
            raise BrowserSessionStateError("unknown browser session")
        if record.extension_id != extension_id:
            raise BrowserSessionStateError("the session belongs to another extension")
        if record.state in _EXPIRABLE_STATES and _is_expired(record, self._now()):
            record = await self._expire(record, "SESSION_EXPIRED")
        return record

    async def _expire(
        self, record: BrowserSessionRecord, code: str
    ) -> BrowserSessionRecord:
        """Release an expired session so a new one can be created."""

        if record.terminal:
            return record
        with contextlib.suppress(Exception):
            await self._companion.close_session(record.session_id)
        return await self._save(
            _replace_state(
                record,
                BrowserSessionState.CANCELLED,
                self._now(),
                outcome="EXPIRED",
                diagnostic_code=code,
            ),
            {},
        )

    async def session_status(
        self, session_id: str, *, extension_id: str
    ) -> tuple[BrowserSessionRecord, CompanionSessionStatus]:
        record = await self.get_session(session_id, extension_id=extension_id)
        raw = _require_mapping(await self._companion.status(session_id), "status")
        status = CompanionSessionStatus(
            session_id=session_id,
            open=bool(raw.get("open", False)),
            url=_require_str(raw.get("url", ""), "status url", max_length=2048),
            origin=_require_str(raw.get("origin", ""), "status origin", max_length=255),
            path=_require_str(raw.get("path", "/"), "status path", max_length=2048),
            login_page=bool(raw.get("login_page", False)),
            headless=bool(raw.get("headless", False)),
            browser_alive=bool(raw.get("browser_alive", False)),
            re_navigations=int(raw.get("re_navigations", 0) or 0),
            blocked_origin_requests=int(raw.get("blocked_origin_requests", 0) or 0),
            blocked_mutating_requests=int(raw.get("blocked_mutating_requests", 0) or 0),
            fill_operations=int(raw.get("fill_operations", 0) or 0),
            click_operations=int(raw.get("click_operations", 0) or 0),
        )
        return record, status

    async def navigate(
        self,
        session_id: str,
        *,
        extension_id: str,
        adapter_id: str,
        transaction_id: str,
        url: str,
    ) -> PageSnapshot:
        record = await self.get_session(session_id, extension_id=extension_id)
        if record.state in _TERMINAL_STATES:
            raise BrowserSessionStateError("the supervised session is not active for this step")
        descriptor = await self._require_adapter(record, adapter_id)
        decision = evaluate_navigation(
            url,
            allowed_origins=self._allowed_origins,
            allowed_paths=_adapter_paths(descriptor),
        )
        if not decision.allowed:
            raise NavigationDeniedError(decision.reason)
        repeated = decision.path in record.visited_paths
        if repeated and record.re_navigations >= self._limits.max_renavigations:
            raise BrowserLimitError("the session reached its re-navigation limit")
        payload = _require_mapping(
            await self._companion.navigate(
                session_id, url, login_paths=tuple(descriptor.login_paths)
            ),
            "navigation",
        )
        login_page = bool(payload.get("login_page", False))
        now = self._now()
        updates: dict[str, Any] = {
            "url": _require_str(payload.get("url", url), "navigation url", max_length=2048),
            "origin": decision.origin,
            "re_navigations": record.re_navigations + (1 if repeated else 0),
            "visited_paths": record.visited_paths
            if repeated
            else (*record.visited_paths, decision.path),
        }
        if login_page and record.state in {
            BrowserSessionState.REQUESTED,
            BrowserSessionState.AUTHENTICATED,
        }:
            target = BrowserSessionState.WAITING_USER
        elif not login_page and record.state is BrowserSessionState.WAITING_USER:
            target = BrowserSessionState.AUTHENTICATED
        else:
            target = record.state
        record = _replace_state(record, target, now, **updates)
        saved = await self._save(record, {})
        return await self._snapshot_impl(saved, descriptor, transaction_id=transaction_id)

    async def snapshot(
        self,
        session_id: str,
        *,
        extension_id: str,
        adapter_id: str = "",
        transaction_id: str = "",
    ) -> tuple[PageSnapshot, RiskAssessment]:
        record = await self.get_session(session_id, extension_id=extension_id)
        self._require_active(record)
        descriptor = await self._require_adapter(record, adapter_id) if adapter_id else None
        snapshot = await self._snapshot_impl(record, descriptor, transaction_id=transaction_id)
        if descriptor is None:
            # A pure page read is not a transaction: it cannot be prohibited by
            # an unknown-transaction rule, only by page text evidence.
            return snapshot, assess_risk(RiskLevel.READ)
        matches, others = _split_signals(snapshot.signals)
        return snapshot, assess_risk(
            descriptor.declared_risk,
            matches=matches,
            extra_signals=others,
            transaction_known=True,
            page_version_known=True,
        )

    async def find_text(
        self, session_id: str, *, extension_id: str, query: str
    ) -> Mapping[str, Any]:
        record = await self.get_session(session_id, extension_id=extension_id)
        self._require_active(record)
        if not query or len(query) > 200:
            raise BrowserLimitError("a text query must be 1-200 characters")
        raw = _require_mapping(await self._companion.find_text(session_id, query), "text search")
        return {
            "found": bool(raw.get("found", False)),
            "count": int(raw.get("count", 0) or 0),
            "excerpt": _require_str(raw.get("excerpt", ""), "text excerpt", max_length=500),
        }

    async def record_discovery(
        self, session_id: str, *, extension_id: str, app_count: int
    ) -> BrowserSessionRecord:
        record = await self.get_session(session_id, extension_id=extension_id)
        if app_count < 0 or app_count > 500:
            raise BrowserLimitError("invalid discovered app count")
        now = self._now()
        if record.state in {BrowserSessionState.REQUESTED, BrowserSessionState.AUTHENTICATED}:
            record = _replace_state(
                record, BrowserSessionState.DISCOVERED, now, apps_count=app_count
            )
        elif record.state is BrowserSessionState.DISCOVERED:
            record = replace(record, updated_at=now, apps_count=app_count)
        else:
            raise BrowserSessionStateError("discovery is not valid in the current state")
        return await self._save(record, {})

    async def record_preparation(
        self,
        session_id: str,
        *,
        extension_id: str,
        adapter_id: str,
        adapter_version: str,
        app_id: str,
        transaction_id: str,
        page_fingerprint: str,
        planned_fields: int,
    ) -> BrowserSessionRecord:
        record = await self.get_session(session_id, extension_id=extension_id)
        self._require_active(record)
        if planned_fields < 0 or planned_fields > self._limits.max_fields:
            raise BrowserLimitError("invalid planned field count")
        registered = await self._adapters.get(extension_id, adapter_id, adapter_version)
        if registered is None:
            raise BrowserPolicyError("UNKNOWN_ADAPTER", "the adapter version is not registered")
        if record.state not in {
            BrowserSessionState.AUTHENTICATED,
            BrowserSessionState.DISCOVERED,
            BrowserSessionState.PREPARING,
            BrowserSessionState.PREVIEW_READY,
            BrowserSessionState.SAFETY_PAUSED,
        }:
            raise BrowserSessionStateError("preparation is not valid in the current state")
        now = self._now()
        record = _replace_state(
            record,
            BrowserSessionState.PREPARING,
            now,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            app_id=app_id,
            transaction_id=transaction_id,
            page_fingerprint=page_fingerprint,
            preview_hash="",
            preview_nonce="",
            preview=None,
        )
        return await self._save(record, {})

    # ------------------------------------------------------------------ actions

    async def execute_navigation(
        self,
        session_id: str,
        *,
        extension_id: str,
        adapter_id: str,
        transaction_id: str,
    ) -> Mapping[str, Any]:
        """Open one declared transaction through its bound navigation action.

        The click is only allowed on the adapter's own ``navigate`` action for a
        declared transaction.  The action must be present on the live page with
        the declared label, the landing path must match and the landing page
        fingerprint must be in the adapter's human-verified set; otherwise the
        session is paused before any fill.  No receipt, no submit, no retry.
        """

        record = await self.get_session(session_id, extension_id=extension_id)
        self._require_active(record)
        if record.state not in {
            BrowserSessionState.AUTHENTICATED,
            BrowserSessionState.DISCOVERED,
        }:
            raise BrowserSessionStateError(
                "opening a transaction requires an authenticated or discovered session"
            )
        descriptor = await self._require_adapter(record, adapter_id)
        if transaction_id not in descriptor.transaction_ids:
            raise BrowserPolicyError("UNKNOWN_TRANSACTION", "the transaction is not registered")
        action = descriptor.navigation_action(transaction_id)
        if action is None:
            raise BrowserPolicyError(
                "NAVIGATION_NOT_DECLARED",
                "the adapter declares no navigation action for this transaction",
            )
        snapshot = await self._snapshot_impl(record, descriptor, transaction_id="")
        live = next(
            (item for item in snapshot.actions if item.locator == action.locator), None
        )
        if live is None or live.label != action.label:
            await self._pause(record, "NAVIGATION_ACTION_DRIFT")
            raise PageDriftError("the navigation action is not on the live page as declared")
        raw = _require_mapping(
            await self._companion.activate(
                session_id, locator=action.locator, expected_path=action.navigates_to_path
            ),
            "navigation activation",
        )
        if int(raw.get("blocked_mutating_requests", 0) or 0) > 0:
            # Opening a transaction must never emit an unapproved write.
            await self._pause(record, "NAVIGATION_WRITE_BLOCKED")
            raise BrowserPolicyError(
                "NAVIGATION_WRITE_BLOCKED",
                "a page-initiated write was blocked during the navigation action",
            )
        landed = _require_str(raw.get("url", ""), "landing url", max_length=2048)
        landing_path = _navigation_path(landed)
        if landing_path != action.navigates_to_path:
            await self._pause(record, "NAVIGATION_MISMATCH")
            raise PageDriftError("the navigation action landed on an unexpected path")
        # Re-read the landing page with the target transaction pinned: an
        # unknown page version pauses the session instead of filling anything.
        landing = await self._snapshot_impl(
            record, descriptor, transaction_id=transaction_id
        )
        if _navigation_path(landing.url) != action.navigates_to_path:
            await self._pause(record, "NAVIGATION_MISMATCH")
            raise PageDriftError("the live page moved after the navigation action")
        # The landing snapshot may have persisted the new URL/state; re-read
        # before writing the transaction binding so the CAS version is current.
        record = await self.get_session(session_id, extension_id=extension_id)
        now = self._now()
        updated = replace(
            record,
            url=landing.url,
            adapter_id=adapter_id,
            adapter_version=descriptor.adapter_version,
            transaction_id=transaction_id,
            page_fingerprint=landing.fingerprint,
            visited_paths=tuple(
                dict.fromkeys((*record.visited_paths, action.navigates_to_path))
            ),
            updated_at=now,
        )
        await self._save(updated, {})
        return {
            "session_id": session_id,
            "transaction_id": transaction_id,
            "url": landing.url,
            "path": action.navigates_to_path,
            "page_fingerprint": landing.fingerprint,
            "state": record.state.value,
        }

    async def execute_fill(
        self,
        session_id: str,
        *,
        task_id: str,
        extension_id: str,
        plan: FillPlan,
    ) -> TransactionPreview:
        record = await self.get_session(session_id, extension_id=extension_id)
        if record.task_id != task_id:
            raise BrowserSessionStateError("the session belongs to another task")
        if record.state in {BrowserSessionState.PREVIEW_READY, BrowserSessionState.APPROVED}:
            if record.preview is not None and record.preview_hash:
                stored = TransactionPreview.from_document(dict(record.preview))
                if not _plan_matches_preview(plan, stored):
                    await self._pause(record, "REPEATED_FILL_DRIFT")
                    raise PageDriftError("a repeated fill does not match the stored preview")
                return stored
            raise BrowserSessionStateError("the session has no stored preview")
        if record.state is not BrowserSessionState.PREPARING:
            raise BrowserSessionStateError("fill requires a prepared session")
        descriptor = await self._require_adapter(record, plan.adapter_id, plan.adapter_version)
        if plan.transaction_id not in descriptor.transaction_ids:
            raise BrowserPolicyError("UNKNOWN_TRANSACTION", "the transaction is not registered")
        snapshot = await self._snapshot_impl(
            record, descriptor, transaction_id=plan.transaction_id
        )
        decision = evaluate_navigation(
            snapshot.url,
            allowed_origins=self._allowed_origins,
            allowed_paths=_adapter_paths(descriptor),
        )
        if not decision.allowed:
            raise NavigationDeniedError(decision.reason)
        if decision.origin != plan.expected_origin:
            raise PageDriftError("the page origin changed since the preview was planned")
        if snapshot.fingerprint != plan.expected_page_fingerprint:
            await self._pause(record, "PAGE_FINGERPRINT_DRIFT")
            raise PageDriftError("the page structure changed since it was inspected")
        matches, others = _split_signals(snapshot.signals)
        risk = assess_risk(
            descriptor.declared_risk,
            matches=matches,
            extra_signals=others,
            transaction_known=True,
            page_version_known=True,
        )
        if risk.prohibited or risk_rank(risk.risk) > risk_rank(descriptor.declared_risk):
            await self._pause(record, "PROHIBITED_SEMANTICS")
            raise ProhibitedTransactionError("PROHIBITED_SEMANTICS")
        try:
            self._verify_fields(descriptor, plan, snapshot)
        except BrowserPolicyError as exc:
            if exc.reason == "UNKNOWN_FIELD":
                await self._pause(record, "UNKNOWN_FIELD")
            raise
        changed = tuple(
            (item.locator, item.new_value)
            for item in plan.fields
            if item.new_value != item.old_value
        )
        if changed:
            await self._companion.fill(session_id, changed)
            after = await self._snapshot_impl(
                record, descriptor, transaction_id=plan.transaction_id
            )
            for item in plan.fields:
                if item.new_value == item.old_value:
                    continue
                live = after.field_by_id(item.field_id)
                if live is None or live.value != item.new_value:
                    await self._fail(record, "FILL_VERIFICATION_FAILED")
                    raise BrowserPolicyError(
                        "FILL_VERIFICATION_FAILED", "the page did not accept a planned value"
                    )
            snapshot = after
        preview = self._build_preview(record, descriptor, plan, snapshot)
        now = self._now()
        record = _replace_state(
            record,
            BrowserSessionState.PREVIEW_READY,
            now,
            page_fingerprint=snapshot.fingerprint,
            preview=preview.to_document(),
            preview_hash=preview.canonical_payload_hash,
            preview_nonce=preview.nonce,
            fill_count=record.fill_count + 1,
        )
        await self._save(record, {})
        return preview

    async def execute_submit(
        self,
        session_id: str,
        *,
        task_id: str,
        extension_id: str,
        preview_hash: str,
        preview_nonce: str,
        action_id: str,
        owner_id: str = "",
    ) -> SubmissionOutcome:
        record = await self.get_session(session_id, extension_id=extension_id)
        if record.task_id != task_id:
            raise BrowserSessionStateError("the session belongs to another task")
        if record.state is not BrowserSessionState.PREVIEW_READY:
            raise BrowserSessionStateError("submit requires a preview-ready session")
        if not self._submit_enabled:
            raise BrowserPolicyError(
                "SUBMIT_DISABLED",
                "real browser submission is disabled on this host",
            )
        if not record.preview or not record.preview_hash or not record.preview_nonce:
            raise BrowserSessionStateError("the session has no stored preview")
        if not hmac.compare_digest(record.preview_hash, preview_hash):
            raise PreviewExpiredError("the submitted preview hash does not match")
        if not hmac.compare_digest(record.preview_nonce, preview_nonce):
            raise PreviewExpiredError("the submitted preview nonce does not match")
        preview = TransactionPreview.from_document(dict(record.preview))
        now = self._now()
        preview.require_fresh(now)
        descriptor = await self._require_adapter(record, record.adapter_id, record.adapter_version)
        final = descriptor.final_action()
        if final is None or final.action_id != action_id:
            raise BrowserPolicyError("ACTION_NOT_ALLOWED", "the action is not the final submit")
        if final.risk is RiskLevel.PROHIBITED:  # pragma: no cover - rejected at registration
            raise ProhibitedTransactionError("PROHIBITED_SEMANTICS")
        if (
            preview.target_action_id != final.action_id
            or preview.target_method.upper() != final.method.upper()
            or preview.target_origin != final.target_origin
            or preview.target_path != final.target_path
        ):
            await self._pause(record, "SUBMIT_TARGET_DRIFT")
            raise PageDriftError("the submission target changed after the preview")
        # The receipt baseline is the structured set of references already on
        # the real tracking page; store only hashes so old receipts can never
        # be mistaken for this submission's result.
        baseline, baseline_truncated = await self._tracking_baseline(record, descriptor)
        snapshot = await self._snapshot_impl(
            record, descriptor, transaction_id=record.transaction_id
        )
        if snapshot.fingerprint != preview.page_fingerprint:
            await self._pause(record, "PAGE_FINGERPRINT_DRIFT")
            raise PageDriftError("the page changed after the preview was generated")
        for change in preview.fields:
            live = snapshot.field_by_id(change.field_id)
            if live is None or live.value != change.new_value:
                await self._pause(record, "PREVIEW_DRIFT")
                raise PageDriftError("a field changed after the preview was generated")
        matches, others = _split_signals(snapshot.signals)
        risk = assess_risk(
            descriptor.declared_risk,
            matches=matches,
            extra_signals=others,
            transaction_known=True,
            page_version_known=True,
        )
        if risk.prohibited or risk_rank(risk.risk) > risk_rank(descriptor.declared_risk):
            await self._pause(record, "PROHIBITED_SEMANTICS")
            raise ProhibitedTransactionError("PROHIBITED_SEMANTICS")
        approved = _replace_state(record, BrowserSessionState.APPROVED, now)
        approved = await self._save(approved, {})
        executing = _replace_state(
            approved,
            BrowserSessionState.EXECUTING,
            self._now(),
            submit_count=record.submit_count + 1,
            owner_id=owner_id,
            receipt_baseline=_baseline_document(baseline, baseline_truncated),
        )
        executing = await self._save(executing, {})
        try:
            raw = await self._companion.click(
                session_id,
                action_id=final.action_id,
                locator=final.locator,
                receipt_locator=descriptor.receipt_locator,
                expected_method=final.method,
                expected_origin=final.target_origin,
                expected_path=final.target_path,
            )
        except Exception as exc:
            await self._transition_terminal(
                executing, BrowserSessionState.UNKNOWN, diagnostic_code="SUBMIT_OUTCOME_LOST"
            )
            del exc
            return await self._converge_submission(session_id, extension_id=extension_id)
        result = _require_mapping(raw, "click result")
        outcome = _require_str(result.get("outcome", "UNKNOWN"), "click outcome", max_length=32)
        if outcome == "REJECTED":
            # The transport answered with a definitive rejection: no retry.
            final_record = _replace_state(
                executing,
                BrowserSessionState.FAILED,
                self._now(),
                outcome="FAILED",
                diagnostic_code="SERVER_REJECTED",
            )
            await self._save(final_record, {})
            return SubmissionOutcome(
                state=BrowserSessionState.FAILED, diagnostic_code="SERVER_REJECTED"
            )
        # The DOM is never authoritative: a page script can fabricate a receipt
        # after a single legitimate POST.  Record UNKNOWN and let the read-only
        # tracking-page diff prove (or refuse) success.
        await self._transition_terminal(
            executing, BrowserSessionState.UNKNOWN, diagnostic_code="SUBMIT_OUTCOME_UNKNOWN"
        )
        return await self._converge_submission(session_id, extension_id=extension_id)

    async def _converge_submission(
        self, session_id: str, *, extension_id: str
    ) -> SubmissionOutcome:
        """Immediately reconcile an UNKNOWN submission through host proof."""

        try:
            record = await self.reconcile(session_id, extension_id=extension_id)
        except BrowserError:
            return SubmissionOutcome(
                state=BrowserSessionState.UNKNOWN,
                diagnostic_code="SUBMIT_UNVERIFIED",
            )
        if record.state is BrowserSessionState.SUCCEEDED and record.receipt:
            receipt = dict(record.receipt)
            return SubmissionOutcome(
                state=BrowserSessionState.SUCCEEDED,
                receipt=receipt,
                reference=str(receipt.get("reference", "")),
            )
        return SubmissionOutcome(
            state=BrowserSessionState.UNKNOWN,
            diagnostic_code=record.diagnostic_code or "SUBMIT_OUTCOME_UNKNOWN",
        )

    async def reconcile(
        self, session_id: str, *, extension_id: str
    ) -> BrowserSessionRecord:
        """Host-driven read-only reconciliation of an UNKNOWN submission.

        The caller cannot declare success: the host performs the tracking-page
        query itself, extracts references that match the adapter's verified
        receipt pattern, excludes anything already present before the click
        (baseline) and only then issues the proof bound to this session,
        transaction and adapter version.
        """

        record = await self.get_session(session_id, extension_id=extension_id)
        if record.state is not BrowserSessionState.UNKNOWN:
            raise BrowserSessionStateError("only an UNKNOWN session can be reconciled")
        descriptor = await self._require_adapter(record, record.adapter_id, record.adapter_version)
        tracking = descriptor.tracking_path
        pattern = descriptor.receipt_pattern
        if not tracking or not pattern:
            raise BrowserPolicyError(
                "TRACKING_NOT_CONFIGURED", "the adapter declares no tracking page"
            )
        url = f"{descriptor.allowed_origins[0]}{tracking}"
        decision = evaluate_navigation(
            url,
            allowed_origins=self._allowed_origins,
            allowed_paths=_adapter_paths(descriptor),
        )
        if not decision.allowed:
            raise NavigationDeniedError(decision.reason)
        raw = _require_mapping(await self._companion.navigate(session_id, url), "navigation")
        live_url = _require_str(raw.get("url", url), "navigation url", max_length=2048)
        live = evaluate_navigation(
            live_url,
            allowed_origins=self._allowed_origins,
            allowed_paths=_adapter_paths(descriptor),
        )
        if not live.allowed:
            raise NavigationDeniedError(live.reason)
        collected = _require_mapping(
            await self._companion.collect_matches(session_id, pattern=pattern, limit=256),
            "tracking matches",
        )
        matches = [
            str(item)[:128] for item in collected.get("matches", ()) if isinstance(item, str)
        ]
        truncated = bool(collected.get("truncated", False))
        baseline, baseline_truncated, baseline_captured = _parse_baseline(
            record.receipt_baseline
        )
        now = self._now()
        if not baseline_captured or baseline_truncated or truncated:
            # Without a provably complete pre-click baseline, a receipt on the
            # tracking page cannot be attributed to this submission.
            return await self._save(
                replace(record, updated_at=now, diagnostic_code="RECONCILE_UNSAFE"),
                {},
            )
        fresh = [
            item
            for item in matches
            if _receipt_hash(item) not in baseline
        ]
        unique = list(dict.fromkeys(fresh))
        if len(unique) == 1:
            reference = unique[0]
            receipt = {
                "reference": reference,
                "issued_by": "host_tracking",
                "session_id": record.session_id,
                "transaction_id": record.transaction_id,
                "adapter_id": descriptor.adapter_id,
                "adapter_version": descriptor.adapter_version,
                "reference_sha256": _receipt_hash(reference),
                "observed_at": now.isoformat(),
            }
            return await self._save(
                _replace_state(
                    record,
                    BrowserSessionState.SUCCEEDED,
                    now,
                    outcome="SUCCEEDED",
                    receipt=receipt,
                    diagnostic_code="RECONCILED",
                ),
                {},
            )
        if len(unique) > 1:
            return await self._save(
                replace(record, updated_at=now, diagnostic_code="RECONCILE_AMBIGUOUS"),
                {},
            )
        return await self._save(
            replace(record, updated_at=now, diagnostic_code="RECONCILE_NOT_FOUND"),
            {},
        )

    async def close_session(self, session_id: str, *, extension_id: str) -> None:
        record = await self.get_session(session_id, extension_id=extension_id)
        try:
            await self._companion.close_session(session_id)
        finally:
            if BrowserSessionState.CANCELLED in allowed_browser_transitions(record.state):
                await self._save(
                    _replace_state(
                        record, BrowserSessionState.CANCELLED, self._now(), outcome="CLOSED"
                    ),
                    {},
                )

    async def cancel_session(self, session_id: str, *, extension_id: str) -> None:
        record = await self.get_session(session_id, extension_id=extension_id)
        try:
            await self._companion.cancel_session(session_id)
        finally:
            if BrowserSessionState.CANCELLED in allowed_browser_transitions(record.state):
                await self._save(
                    _replace_state(
                        record, BrowserSessionState.CANCELLED, self._now(), outcome="CANCELLED"
                    ),
                    {},
                )

    async def recover_stale_sessions(
        self, *, active_owners: Sequence[str] = ()
    ) -> tuple[str, ...]:
        return await self._sessions.recover_stale_executions(active_owners=active_owners)

    async def aclose(self) -> None:
        await self._companion.aclose()

    # ------------------------------------------------------------------ internals

    async def _tracking_baseline(
        self,
        record: BrowserSessionRecord,
        descriptor: TransactionAdapterDescriptor,
    ) -> tuple[tuple[str, ...], bool]:
        """Read the real tracking page and hash every reference already there."""

        tracking = descriptor.tracking_path
        pattern = descriptor.receipt_pattern
        if not tracking or not pattern:
            return (), False
        url = f"{descriptor.allowed_origins[0]}{tracking}"
        decision = evaluate_navigation(
            url,
            allowed_origins=self._allowed_origins,
            allowed_paths=_adapter_paths(descriptor),
        )
        if not decision.allowed:
            raise NavigationDeniedError(decision.reason)
        collected = _require_mapping(
            await self._companion.collect_matches(
                record.session_id, pattern=pattern, limit=128, url=url
            ),
            "tracking matches",
        )
        matches = [
            str(item)[:128]
            for item in collected.get("matches", ())
            if isinstance(item, str)
        ]
        truncated = bool(collected.get("truncated", False))
        return tuple(_receipt_hash(item) for item in matches), truncated

    async def _snapshot_impl(
        self,
        record: BrowserSessionRecord,
        descriptor: TransactionAdapterDescriptor | None,
        *,
        transaction_id: str = "",
    ) -> PageSnapshot:
        terms = descriptor.forbidden_terms if descriptor else ()
        raw = _require_mapping(
            await self._companion.snapshot(
                record.session_id,
                prohibited_terms=tuple(terms),
                scan_text=descriptor is not None,
                login_paths=tuple(descriptor.login_paths) if descriptor else (),
            ),
            "snapshot",
        )
        if len(canonical_json(raw).encode("utf-8")) > self._limits.max_snapshot_bytes:
            raise BrowserLimitError("the page snapshot exceeds the byte limit")
        url = _require_str(raw.get("url", ""), "snapshot url", max_length=2048)
        title = _require_str(raw.get("title", ""), "snapshot title", max_length=500)
        structure = _require_mapping(raw.get("structure", {}), "snapshot structure")
        controls_raw = structure.get("controls", [])
        headings_raw = structure.get("headings", [])
        if not isinstance(controls_raw, Sequence) or not isinstance(headings_raw, Sequence):
            raise BrowserPolicyError("COMPANION_MALFORMED", "snapshot structure is malformed")
        if len(controls_raw) > self._limits.max_fields * 8:
            raise BrowserLimitError("the page has too many controls")
        raw_controls = [item for item in controls_raw if isinstance(item, Mapping)]
        links_raw = raw.get("links", ())
        if not isinstance(links_raw, Sequence):
            links_raw = ()
        raw_links = [item for item in list(links_raw)[:128] if isinstance(item, Mapping)]
        forms_raw = structure.get("forms", ())
        if not isinstance(forms_raw, Sequence):
            forms_raw = ()
        raw_forms = [item for item in list(forms_raw)[:32] if isinstance(item, Mapping)]
        document = page_structure_document(
            controls=raw_controls,
            headings=[item for item in headings_raw if isinstance(item, Mapping)],
            links=raw_links,
            forms=raw_forms,
        )
        fingerprint = compute_page_fingerprint(document)
        decision = evaluate_navigation(
            url,
            allowed_origins=self._allowed_origins,
            allowed_paths=_adapter_paths(descriptor) if descriptor else (),
        )
        if not decision.allowed:
            raise NavigationDeniedError(decision.reason)
        # Only the adapter's own transaction page is version-pinned: SSO and
        # discovery pages are covered by the origin/path allowlist, login
        # detection and the prohibited-term scan.
        if (
            descriptor is not None
            and _is_transaction_page(descriptor, transaction_id, decision.path)
            and fingerprint not in descriptor.allowed_page_fingerprints
        ):
            await self._pause(record, "UNKNOWN_PAGE_VERSION")
            raise ProhibitedTransactionError(
                "UNKNOWN_PAGE_VERSION",
                "the live page version is not in the adapter's verified fingerprint set",
            )
        if descriptor is not None and bool(raw.get("scan_incomplete", False)):
            await self._pause(record, "PAGE_TEXT_SCAN_INCOMPLETE")
            raise ProhibitedTransactionError(
                "PAGE_TEXT_SCAN_INCOMPLETE",
                "the page text could not be fully scanned; failing closed",
            )
        live_by_locator: dict[str, Mapping[str, Any]] = {}
        for item in raw_controls:
            locator = str(item.get("locator", ""))
            if not locator:
                continue
            if locator in live_by_locator:
                raise BrowserLimitError("the page has ambiguous field locators")
            live_by_locator[locator] = item
        fields: list[PageField] = []
        for locator, item in live_by_locator.items():
            spec = descriptor.field_spec_by_locator(locator) if descriptor else None
            fields.append(
                PageField(
                    field_id=spec.field_id if spec else "",
                    locator=locator,
                    kind=str(item.get("type") or item.get("tag") or "unknown"),
                    value=str(item.get("value", "")),
                    name=str(item.get("name", "")),
                    required=bool(item.get("required", False)),
                    readonly=bool(item.get("readonly", False)),
                    options=tuple(str(option) for option in item.get("options", ())),
                    max_length=int(item.get("max_length", 0) or 0),
                    known=spec is not None,
                )
            )
        actions = self._parse_actions(raw.get("actions", ()), descriptor)
        signals = self._parse_signals(raw.get("signals", ()))
        login_page = bool(raw.get("login_page", False))
        now = self._now()
        state = record.state
        if login_page and state in {
            BrowserSessionState.REQUESTED,
            BrowserSessionState.AUTHENTICATED,
            BrowserSessionState.DISCOVERED,
            BrowserSessionState.PREPARING,
            BrowserSessionState.PREVIEW_READY,
        }:
            state = BrowserSessionState.WAITING_USER
        elif not login_page and state in {
            BrowserSessionState.REQUESTED,
            BrowserSessionState.WAITING_USER,
        }:
            state = BrowserSessionState.AUTHENTICATED
        origin = _origin(url)
        if state is not record.state or record.url != url or record.origin != origin:
            record = _replace_state(record, state, now, url=url, origin=origin)
            record = await self._save(record, {})
        del transaction_id
        return PageSnapshot(
            session_id=record.session_id,
            url=url,
            origin=origin,
            path=_path_only(url),
            title=title,
            fingerprint=fingerprint,
            captured_at=now,
            fields=tuple(fields),
            actions=actions,
            signals=signals,
            links=tuple(
                PageLink(
                    text=_require_str(item.get("text", ""), "link text", max_length=200),
                    path=_require_str(item.get("path", ""), "link path", max_length=2048),
                )
                for item in raw_links
            ),
            text_digest=_require_str(raw.get("text_digest", ""), "text digest", max_length=64),
            byte_size=int(raw.get("byte_size", 0) or 0),
            truncated=bool(raw.get("truncated", False)),
            scan_incomplete=bool(raw.get("scan_incomplete", False)),
            authenticated=not login_page,
        )

    def _parse_actions(
        self,
        raw: Any,
        descriptor: TransactionAdapterDescriptor | None,
    ) -> tuple[PageAction, ...]:
        if not isinstance(raw, Sequence):
            return ()
        result: list[PageAction] = []
        for item in list(raw)[:MAX_ACTIONS]:
            if not isinstance(item, Mapping):
                continue
            locator = str(item.get("locator", ""))
            if not locator:
                continue
            spec = descriptor.action_by_locator(locator) if descriptor else None
            result.append(
                PageAction(
                    action_id=spec.action_id if spec else "",
                    locator=locator,
                    label=str(item.get("label", ""))[:200],
                    kind=str(item.get("kind", "other"))[:32],
                    risk=spec.risk if spec else RiskLevel.EXTERNAL_WRITE,
                    known=spec is not None,
                )
            )
        return tuple(result)

    def _parse_signals(self, raw: Any) -> tuple[RiskSignal, ...]:
        if not isinstance(raw, Sequence):
            return ()
        signals: list[RiskSignal] = []
        for item in list(raw)[:64]:
            if not isinstance(item, Mapping):
                continue
            code = str(item.get("code", ""))[:64]
            detail = str(item.get("detail", ""))[:200]
            try:
                risk = RiskLevel(str(item.get("risk", "EXTERNAL_WRITE")))
            except ValueError:
                risk = RiskLevel.PROHIBITED
            if code:
                signals.append(RiskSignal(code=code, detail=detail, risk=risk))
        return tuple(signals)

    def _verify_fields(
        self,
        descriptor: TransactionAdapterDescriptor,
        plan: FillPlan,
        snapshot: PageSnapshot,
    ) -> None:
        if len(plan.fields) > self._limits.max_fields:
            raise BrowserLimitError("the fill plan exceeds the field limit")
        if len(plan.attachments) > self._limits.max_attachments:
            raise BrowserLimitError("the fill plan exceeds the attachment limit")
        for attachment in plan.attachments:
            if attachment.size_bytes > self._limits.max_attachment_bytes:
                raise BrowserLimitError("an attachment exceeds the size limit")
        planned_specs: set[str] = set()
        for change in plan.fields:
            spec = descriptor.field_spec(change.field_id)
            if spec is None:
                raise BrowserPolicyError("UNKNOWN_FIELD", "the plan contains an unknown field")
            if spec.locator != change.locator:
                raise PageDriftError("the planned locator no longer matches the adapter")
            live = snapshot.field_by_id(change.field_id)
            if live is None:
                raise PageDriftError("a planned field is missing from the live page")
            if live.value != change.old_value:
                raise PageDriftError("a field changed since the preview was planned")
            if change.new_value:
                if spec.max_length and len(change.new_value) > spec.max_length:
                    raise BrowserPolicyError("INVALID_FIELD_VALUE", "a value exceeds its limit")
                if spec.pattern and not re.fullmatch(spec.pattern, change.new_value):
                    raise BrowserPolicyError("INVALID_FIELD_VALUE", "a value fails validation")
                if live.options and change.new_value not in live.options:
                    raise BrowserPolicyError(
                        "INVALID_FIELD_VALUE", "a select value is not an option"
                    )
            elif spec.required:
                raise BrowserPolicyError("MISSING_REQUIRED_FIELD", "a required field is empty")
            planned_specs.add(change.field_id)
        for spec in descriptor.fields:
            if spec.required and spec.field_id not in planned_specs:
                raise BrowserPolicyError("MISSING_REQUIRED_FIELD", "a required field is missing")
        unknown = [
            field
            for field in snapshot.fields
            if not field.known and field.kind not in {"hidden", "button", "submit"}
        ]
        if unknown:
            raise BrowserPolicyError(
                "UNKNOWN_FIELD", "the live page contains fields the adapter does not know"
            )

    def _build_preview(
        self,
        record: BrowserSessionRecord,
        descriptor: TransactionAdapterDescriptor,
        plan: FillPlan,
        snapshot: PageSnapshot,
    ) -> TransactionPreview:
        now = self._now()
        changes = tuple(
            FieldChange(
                field_id=item.field_id,
                locator=item.locator,
                label=item.label or _label_for(descriptor, item.field_id),
                old_value=item.old_value,
                new_value=item.new_value,
                source=item.source,
                confidence=item.confidence,
                validation=(FieldValidation.VALID if item.new_value else FieldValidation.MISSING),
                evidence_sha256=item.evidence_sha256,
            )
            for item in plan.fields
        )
        missing = tuple(
            spec.field_id
            for spec in descriptor.fields
            if spec.required and spec.field_id not in plan.field_ids()
        )
        attachments = tuple(
            AttachmentPreview(
                name=item.name,
                size_bytes=item.size_bytes,
                sha256=item.sha256.lower(),
                media_type=item.media_type,
            )
            for item in plan.attachments
        )
        consequences = plan.consequences or descriptor.consequences
        final = descriptor.final_action()
        digest = canonical_preview_sha256(
            origin=snapshot.origin,
            app_id=plan.app_id,
            transaction_id=plan.transaction_id,
            adapter_id=descriptor.adapter_id,
            adapter_version=descriptor.adapter_version,
            extension_id=descriptor.extension_id,
            extension_version=descriptor.extension_version,
            page_fingerprint=snapshot.fingerprint,
            risk=descriptor.declared_risk,
            consequences=consequences,
            fields=changes,
            attachments=attachments,
            target_action_id=final.action_id if final else "",
            target_method=final.method if final else "",
            target_origin=final.target_origin if final else "",
            target_path=final.target_path if final else "",
        )
        return TransactionPreview(
            session_id=record.session_id,
            origin=snapshot.origin,
            app_id=plan.app_id,
            transaction_id=plan.transaction_id,
            adapter_id=descriptor.adapter_id,
            adapter_version=descriptor.adapter_version,
            extension_id=descriptor.extension_id,
            extension_version=descriptor.extension_version,
            page_fingerprint=snapshot.fingerprint,
            risk=descriptor.declared_risk,
            consequences=consequences,
            fields=changes,
            missing_fields=missing,
            unknown_fields=(),
            attachments=attachments,
            canonical_payload_hash=digest,
            nonce=self._nonce_factory(),
            generated_at=now,
            expires_at=now + _PREVIEW_TTL,
            target_action_id=final.action_id if final else "",
            target_method=final.method if final else "",
            target_origin=final.target_origin if final else "",
            target_path=final.target_path if final else "",
        )

    async def _require_adapter(
        self,
        record: BrowserSessionRecord,
        adapter_id: str,
        adapter_version: str = "",
    ) -> TransactionAdapterDescriptor:
        if not adapter_id:
            raise BrowserPolicyError("UNKNOWN_ADAPTER", "an adapter id is required")
        if record.adapter_id and adapter_id != record.adapter_id:
            raise BrowserPolicyError("UNKNOWN_ADAPTER", "another adapter owns this session")
        version = adapter_version or record.adapter_version
        if version:
            registered = await self._adapters.get(record.extension_id, adapter_id, version)
        else:
            candidates = [
                item
                for item in await self._adapters.list_for_extension(record.extension_id)
                if item.adapter_id == adapter_id
            ]
            registered = (
                max(candidates, key=lambda item: item.updated_at) if candidates else None
            )
        if registered is None:
            raise BrowserPolicyError("UNKNOWN_ADAPTER", "the adapter is not registered")
        descriptor = descriptor_from_document(registered.descriptor)
        if descriptor.extension_version != record.extension_version:
            raise BrowserSessionStateError(
                "the extension was upgraded during a supervised session"
            )
        return descriptor

    async def _save(
        self, record: BrowserSessionRecord, updates: dict[str, Any]
    ) -> BrowserSessionRecord:
        if updates:
            record = replace(record, **updates)
        return await self._sessions.save(record, expected_version=record.version)

    async def _pause(self, record: BrowserSessionRecord, code: str) -> None:
        if record.state is not BrowserSessionState.SAFETY_PAUSED:
            await self._save(
                _replace_state(
                    record,
                    BrowserSessionState.SAFETY_PAUSED,
                    self._now(),
                    diagnostic_code=code,
                ),
                {},
            )

    async def _fail(self, record: BrowserSessionRecord, code: str) -> None:
        if not record.terminal:
            await self._save(
                _replace_state(
                    record, BrowserSessionState.FAILED, self._now(), diagnostic_code=code
                ),
                {},
            )

    async def _transition_terminal(
        self,
        record: BrowserSessionRecord,
        target: BrowserSessionState,
        *,
        diagnostic_code: str,
    ) -> BrowserSessionRecord:
        return await self._save(
            _replace_state(record, target, self._now(), diagnostic_code=diagnostic_code),
            {},
        )

    def _require_active(self, record: BrowserSessionRecord) -> None:
        if record.state in {
            BrowserSessionState.SUCCEEDED,
            BrowserSessionState.FAILED,
            BrowserSessionState.CANCELLED,
            BrowserSessionState.WAITING_USER,
        } or (record.state in _EXPIRABLE_STATES and _is_expired(record, self._now())):
            raise BrowserSessionStateError("the supervised session is not active for this step")


def _receipt_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _baseline_document(hashes: Sequence[str], truncated: bool) -> str:
    return json.dumps(
        {
            "captured": bool(hashes) or not truncated,
            "hashes": sorted(set(hashes)),
            "truncated": bool(truncated),
        },
        separators=(",", ":"),
    )


def _parse_baseline(text: str) -> tuple[frozenset[str], bool, bool]:
    """Return ``(hashes, truncated, captured)`` for a stored baseline."""

    if not text:
        return frozenset(), False, False
    try:
        document = json.loads(text)
    except ValueError:
        return frozenset(), False, False
    if not isinstance(document, Mapping):
        return frozenset(), False, False
    hashes = document.get("hashes")
    if not isinstance(hashes, list):
        return frozenset(), False, False
    return (
        frozenset(str(item) for item in hashes if isinstance(item, str)),
        bool(document.get("truncated", False)),
        True,
    )


def _navigation_path(url: str) -> str:
    """Path plus optional hash route, ignoring the query string."""

    parts = urlsplit(url)
    path = parts.path or "/"
    return f"{path}#{parts.fragment}" if parts.fragment else path


def _is_transaction_page(
    descriptor: TransactionAdapterDescriptor, transaction_id: str, path: str
) -> bool:
    """True when the snapshot belongs to the adapter's version-pinned page.

    Only paths the adapter declares as transaction paths are pinned; SSO and
    discovery pages keep working while still being covered by the origin/path
    allowlist, login detection and the prohibited-term scan.
    """

    return (
        bool(transaction_id)
        and transaction_id in descriptor.transaction_ids
        and path in descriptor.allowed_paths
    )


def _is_expired(record: BrowserSessionRecord, now: datetime) -> bool:
    return not record.terminal and now >= record.expires_at


def _receipt_query(receipt_locator: str) -> str:
    if receipt_locator.startswith("text:"):
        return receipt_locator[5:].strip()
    return ""


def _replace_state(
    record: BrowserSessionRecord,
    target: BrowserSessionState,
    now: datetime,
    **updates: Any,
) -> BrowserSessionRecord:
    if target is not record.state:
        ensure_browser_transition(record.state, target)
    return replace(record, state=target, updated_at=now, **updates)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port
    if port in (None, 443):
        return f"https://{host}"
    return f"https://{host}:{port}"


def _path_only(url: str) -> str:
    return urlsplit(url).path or "/"


def _adapter_paths(descriptor: TransactionAdapterDescriptor) -> tuple[str, ...]:
    paths = list(descriptor.allowed_paths)
    for extra in (*descriptor.login_paths, descriptor.discovery_path, descriptor.tracking_path):
        if extra and extra not in paths:
            paths.append(extra)
    return tuple(paths)


def _label_for(descriptor: TransactionAdapterDescriptor, field_id: str) -> str:
    spec = descriptor.field_spec(field_id)
    return spec.label if spec else field_id


def _split_signals(
    signals: tuple[RiskSignal, ...],
) -> tuple[tuple[ProhibitedMatch, ...], tuple[RiskSignal, ...]]:
    matches: list[ProhibitedMatch] = []
    others: list[RiskSignal] = []
    for signal in signals:
        if signal.code == "PROHIBITED_TERM":
            for category in ProhibitedCategory:
                if category.value == signal.detail:
                    matches.append(ProhibitedMatch(category, category.value, (0, 0)))
        else:
            others.append(signal)
    return tuple(matches), tuple(others)


def _plan_matches_preview(plan: FillPlan, preview: TransactionPreview) -> bool:
    if plan.transaction_id != preview.transaction_id:
        return False
    if plan.expected_page_fingerprint != preview.page_fingerprint:
        return False
    stored = {item.field_id: (item.old_value, item.new_value) for item in preview.fields}
    planned = {item.field_id: (item.old_value, item.new_value) for item in plan.fields}
    return stored == planned


def _receipt_reference(text: str, transaction_id: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return f"{transaction_id}:receipt"
    return cleaned[:200]


def _descriptor_document(descriptor: TransactionAdapterDescriptor) -> dict[str, Any]:
    return {
        "extension_id": descriptor.extension_id,
        "extension_version": descriptor.extension_version,
        "adapter_id": descriptor.adapter_id,
        "adapter_version": descriptor.adapter_version,
        "display_name": descriptor.display_name,
        "allowed_origins": list(descriptor.allowed_origins),
        "allowed_paths": list(descriptor.allowed_paths),
        "declared_risk": descriptor.declared_risk.value,
        "transaction_ids": list(descriptor.transaction_ids),
        "fields": [
            {
                "field_id": item.field_id,
                "label": item.label,
                "kind": item.kind,
                "required": item.required,
                "max_length": item.max_length,
                "pattern": item.pattern,
                "locator": item.locator,
                "source": item.source.value,
            }
            for item in descriptor.fields
        ],
        "actions": [
            {
                "action_id": item.action_id,
                "locator": item.locator,
                "label": item.label,
                "kind": item.kind,
                "risk": item.risk.value,
                "final": item.final,
                "method": item.method,
                "target_origin": item.target_origin,
                "target_path": item.target_path,
                "transaction_id": item.transaction_id,
                "navigates_to_path": item.navigates_to_path,
            }
            for item in descriptor.actions
        ],
        "login_paths": list(descriptor.login_paths),
        "forbidden_terms": list(descriptor.forbidden_terms),
        "discovery_path": descriptor.discovery_path,
        "tracking_path": descriptor.tracking_path,
        "receipt_locator": descriptor.receipt_locator,
        "receipt_pattern": descriptor.receipt_pattern,
        "allowed_page_fingerprints": list(descriptor.allowed_page_fingerprints),
        "consequences": descriptor.consequences,
    }


def descriptor_from_document(document: Mapping[str, Any]) -> TransactionAdapterDescriptor:
    fields: list[AdapterFieldSpec] = []
    for item in document.get("fields", []):
        if not isinstance(item, Mapping):
            continue
        fields.append(
            AdapterFieldSpec(
                field_id=str(item["field_id"]),
                label=str(item.get("label", "")),
                kind=str(item.get("kind", "text")),
                required=bool(item.get("required", True)),
                max_length=int(item.get("max_length", 0) or 0),
                pattern=str(item.get("pattern", "")),
                locator=str(item.get("locator", "")),
                source=FieldValueSource(str(item.get("source", "USER_INPUT"))),
            )
        )
    actions: list[AdapterActionSpec] = []
    for item in document.get("actions", []):
        if not isinstance(item, Mapping):
            continue
        actions.append(
            AdapterActionSpec(
                action_id=str(item["action_id"]),
                locator=str(item.get("locator", "")),
                label=str(item.get("label", "")),
                kind=str(item.get("kind", "submit")),
                risk=RiskLevel(str(item.get("risk", "EXTERNAL_WRITE"))),
                final=bool(item.get("final", False)),
                method=str(item.get("method", "")),
                target_origin=str(item.get("target_origin", "")),
                target_path=str(item.get("target_path", "")),
                transaction_id=str(item.get("transaction_id", "")),
                navigates_to_path=str(item.get("navigates_to_path", "")),
            )
        )
    return TransactionAdapterDescriptor(
        extension_id=str(document["extension_id"]),
        extension_version=str(document["extension_version"]),
        adapter_id=str(document["adapter_id"]),
        adapter_version=str(document["adapter_version"]),
        display_name=str(document.get("display_name", "")),
        allowed_origins=tuple(str(item) for item in document.get("allowed_origins", [])),
        allowed_paths=tuple(str(item) for item in document.get("allowed_paths", [])),
        declared_risk=RiskLevel(str(document["declared_risk"])),
        transaction_ids=tuple(str(item) for item in document.get("transaction_ids", [])),
        fields=tuple(fields),
        actions=tuple(actions),
        login_paths=tuple(str(item) for item in document.get("login_paths", [])),
        forbidden_terms=tuple(str(item) for item in document.get("forbidden_terms", [])),
        discovery_path=str(document.get("discovery_path", "")),
        tracking_path=str(document.get("tracking_path", "")),
        receipt_locator=str(document.get("receipt_locator", "")),
        consequences=str(document.get("consequences", "")),
        allowed_page_fingerprints=tuple(
            str(item) for item in document.get("allowed_page_fingerprints", [])
        ),
        receipt_pattern=str(document.get("receipt_pattern", "")),
    )


__all__ = ["SESSION_TTL", "BrowserSessionBroker", "descriptor_from_document"]
