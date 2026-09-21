"""Shared F07 test support: a deterministic Desktop Companion double.

This is *not* the browser: it implements the vendor-neutral
``DesktopBrowserPort`` with an in-memory page model so unit tests can exercise
the broker, policy and executor without Playwright.  Real-browser coverage
lives in ``tests/integration/test_browser_companion_real_f07.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from personal_assistant.core.browser import (
    AdapterActionSpec,
    AdapterFieldSpec,
    FieldChange,
    FieldValueSource,
    FillPlan,
    TransactionAdapterDescriptor,
    compute_page_fingerprint,
    page_structure_document,
)
from personal_assistant.domain.enums import RiskLevel

TEST_ORIGIN = "https://ehall.test.example"
TEST_DISCOVERY_PATH = "/portal"
TEST_APP_PATH = "/apps/proof"
TEST_LOGIN_PATH = "/sso/login"
TEST_TRACKING_PATH = "/apps/proof/status"
TEST_SUBMIT_PATH = "/apps/proof/submit"
TEST_TRANSCRIPT_PATH = "/apps/transcript"
TEST_TRANSCRIPT_SUBMIT_PATH = "/apps/transcript/submit"


@dataclass
class FakeField:
    locator: str
    kind: str = "text"
    value: str = ""
    name: str = ""
    required: bool = False
    readonly: bool = False
    options: tuple[str, ...] = ()
    max_length: int = 0
    tag: str = "input"


@dataclass
class FakeAction:
    locator: str
    label: str
    kind: str = "submit"


@dataclass
class FakePage:
    path: str
    title: str = "Page"
    login_page: bool = False
    fields: list[FakeField] = field(default_factory=list)
    actions: list[FakeAction] = field(default_factory=list)
    signals: list[dict[str, str]] = field(default_factory=list)
    body_text: str = ""
    heading: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    forms: list[dict[str, str]] = field(default_factory=list)


@dataclass
class FakeCompanion:
    origin: str = TEST_ORIGIN
    click_outcome: str = "RECEIPT"
    receipt_text: str = "回执号 NJU-2026-0001"
    click_error: Exception | None = None
    fill_error: Exception | None = None
    navigate_error: Exception | None = None
    snapshot_error: Exception | None = None
    pages: dict[str, FakePage] = field(default_factory=dict)
    default_page: FakePage | None = None
    headless: bool = False
    scan_incomplete: bool = False
    redirect_to: str = ""
    activate_landing: str = ""
    activate_blocked_writes: int = 0
    click_write_requests: int = 1
    tracking_matches: list[str] = field(default_factory=list)
    tracking_truncated: bool = False
    receipt_appears_after_click: str = ""

    def __post_init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.navigations: list[tuple[str, str]] = []
        self.fill_calls: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        self.click_calls: list[tuple[str, str, str]] = []
        self.click_targets: list[tuple[str, str, str]] = []
        self.activate_calls: list[tuple[str, str, str]] = []
        self.collect_urls: list[str] = []
        self.submitted = False
        self.closed: list[str] = []
        self.cancelled: list[str] = []
        self.autosave_attempts = 0
        self.found_text = ""
        self.blocked_origin_requests = 0
        self.blocked_mutating_requests = 0
        if not self.pages:
            self.pages = {TEST_DISCOVERY_PATH: FakePage(TEST_DISCOVERY_PATH, "Portal")}
        if self.default_page is None:
            self.default_page = FakePage("/unknown", "Unknown")

    # -- DesktopBrowserPort ------------------------------------------------

    async def create_session(
        self,
        *,
        session_id: str,
        purpose: str,
        allowed_origins: tuple[str, ...],
        task_id: str,
        extension_id: str,
    ) -> Mapping[str, Any]:
        self.sessions[session_id] = {"url": "", "page": None, "purpose": purpose}
        return {"url": "", "origin": "", "session_id": session_id}

    async def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)
        self.sessions.pop(session_id, None)

    async def cancel_session(self, session_id: str) -> None:
        self.cancelled.append(session_id)
        self.sessions.pop(session_id, None)

    async def status(self, session_id: str) -> Mapping[str, Any]:
        session = self.sessions.get(session_id)
        if session is None:
            return self._status_payload(session_id, open=False)
        return self._status_payload(session_id, open=True)

    def _status_payload(self, session_id: str, *, open: bool) -> dict[str, Any]:
        session = self.sessions.get(session_id, {})
        url = str(session.get("url", ""))
        page: FakePage | None = session.get("page")
        return {
            "session_id": session_id,
            "open": open,
            "url": url,
            "origin": self.origin if url else "",
            "path": _path(url) if url else "/",
            "login_page": bool(page.login_page) if page else False,
            "headless": self.headless,
            "browser_alive": open,
            "re_navigations": len(self.navigations),
            "blocked_origin_requests": self.blocked_origin_requests,
            "blocked_mutating_requests": self.blocked_mutating_requests,
            "fill_operations": len(self.fill_calls),
            "click_operations": len(self.click_calls),
        }

    async def navigate(
        self, session_id: str, url: str, *, login_paths: tuple[str, ...] = ()
    ) -> Mapping[str, Any]:
        del login_paths
        if self.navigate_error is not None:
            raise self.navigate_error
        self.navigations.append((session_id, url))
        page = self.page_for(url)
        landed = self.redirect_to or url
        self.sessions[session_id] = {"url": landed, "page": page, "purpose": "supervised"}
        return {
            "url": landed,
            "origin": self.origin,
            "path": _path(landed),
            "login_page": page.login_page,
        }

    async def snapshot(
        self,
        session_id: str,
        *,
        prohibited_terms: tuple[str, ...] = (),
        scan_text: bool = False,
        login_paths: tuple[str, ...] = (),
    ) -> Mapping[str, Any]:
        del login_paths
        if self.snapshot_error is not None:
            raise self.snapshot_error
        session = self.sessions[session_id]
        url = str(session["url"])
        page: FakePage = session["page"]
        signals = [dict(item) for item in page.signals]
        if scan_text and page.body_text:
            from personal_assistant.core.browser import scan_text_for_prohibited_terms

            for match in scan_text_for_prohibited_terms(page.body_text, prohibited_terms):
                signals.append(
                    {
                        "code": "PROHIBITED_TERM",
                        "detail": match.category.value,
                        "risk": RiskLevel.PROHIBITED.value,
                    }
                )
        controls = _controls_document(page.fields)
        headings = [{"level": 1, "text": page.heading}] if page.heading else []
        return {
            "url": url,
            "title": page.title,
            "login_page": page.login_page,
            "structure": {
                "controls": controls,
                "headings": headings,
                "forms": [dict(item) for item in page.forms],
            },
            "actions": [
                {"locator": item.locator, "label": item.label, "kind": item.kind}
                for item in page.actions
            ],
            "links": [dict(item) for item in page.links],
            "signals": signals,
            "text_digest": "0" * 64,
            "byte_size": 2048,
            "truncated": self.scan_incomplete,
            "scan_incomplete": self.scan_incomplete,
        }

    async def activate(
        self, session_id: str, *, locator: str, expected_path: str
    ) -> Mapping[str, Any]:
        from urllib.parse import urljoin

        self.activate_calls.append((session_id, locator, expected_path))
        target = self.activate_landing or expected_path
        landed = target if target.startswith("http") else urljoin(self.origin, target)
        page = self.page_for(landed)
        self.sessions[session_id] = {"url": landed, "page": page, "purpose": "supervised"}
        return {
            "clicked": True,
            "url": landed,
            "path": _path(landed),
            "blocked_mutating_requests": self.activate_blocked_writes,
        }

    async def collect_matches(
        self, session_id: str, *, pattern: str, limit: int, url: str = ""
    ) -> Mapping[str, Any]:
        import re

        if url:
            self.collect_urls.append(url)
        compiled = re.compile(pattern)
        visible = list(self.tracking_matches)
        if self.submitted and self.receipt_appears_after_click:
            visible.append(self.receipt_appears_after_click)
        matches = [item[:128] for item in visible if compiled.search(item)][:limit]
        return {"matches": matches, "truncated": self.tracking_truncated}

    async def fill(
        self, session_id: str, fields: tuple[tuple[str, str], ...]
    ) -> Mapping[str, Any]:
        if self.fill_error is not None:
            raise self.fill_error
        self.fill_calls.append((session_id, fields))
        session = self.sessions[session_id]
        page: FakePage = session["page"]
        for locator, value in fields:
            for item in page.fields:
                if item.locator == locator:
                    item.value = value
        return {"applied": len(fields)}

    async def find_text(self, session_id: str, query: str) -> Mapping[str, Any]:
        if self.found_text:
            return {"found": True, "count": 1, "excerpt": self.found_text[:500]}
        session = self.sessions[session_id]
        page: FakePage = session["page"]
        if query and query in page.body_text:
            return {"found": True, "count": 1, "excerpt": page.body_text[:500]}
        return {"found": False, "count": 0, "excerpt": ""}

    async def click(
        self,
        session_id: str,
        *,
        action_id: str,
        locator: str,
        receipt_locator: str,
        expected_method: str,
        expected_origin: str,
        expected_path: str,
    ) -> Mapping[str, Any]:
        self.click_calls.append((session_id, action_id, locator))
        self.click_targets.append((expected_method, expected_origin, expected_path))
        self.submitted = True
        if self.click_error is not None:
            raise self.click_error
        session = self.sessions[session_id]
        return {
            "clicked": True,
            "outcome": self.click_outcome,
            "url": str(session.get("url", "")),
            "receipt_found": self.click_outcome == "RECEIPT",
            "receipt_text": self.receipt_text if self.click_outcome == "RECEIPT" else "",
            "write_requests_allowed": (
                self.click_write_requests if self.click_outcome == "RECEIPT" else 0
            ),
        }

    async def aclose(self) -> None:
        return None

    # -- helpers -----------------------------------------------------------

    def page_for(self, url: str) -> FakePage:
        path = _path(url)
        if path in self.pages:
            return self.pages[path]
        assert self.default_page is not None
        return self.default_page


def _controls_document(fields: Sequence[FakeField]) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    for item in fields:
        controls.append(
            {
                "locator": item.locator,
                "tag": item.tag,
                "type": item.kind,
                "name": item.name,
                "value": item.value,
                "required": item.required,
                "readonly": item.readonly,
                "options": list(item.options),
                "max_length": item.max_length,
            }
        )
    return controls


def app_page_fingerprint(
    *,
    extra_fields: Sequence[FakeField] = (),
    forms: Sequence[Mapping[str, str]] = (),
) -> str:
    """The canonical fingerprint of the standard mock application page."""

    document = page_structure_document(
        controls=_controls_document([*DEFAULT_APP_FIELDS, *extra_fields]),
        headings=[{"level": 1, "text": "在读证明申请"}],
        links=(),
        forms=forms,
    )
    return compute_page_fingerprint(document)


DEFAULT_APP_FIELDS: tuple[FakeField, ...] = (
    FakeField("ctl:0:0", value="", name="reason", required=True, max_length=200),
    FakeField("ctl:0:1", value="", name="phone", required=True, max_length=20, tag="input"),
    FakeField(
        "ctl:1:0",
        kind="select",
        value="paper",
        name="delivery",
        required=True,
        options=("paper", "email"),
        tag="select",
    ),
)


def _path(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).path or "/"


def make_page(
    path: str,
    *,
    title: str = "Page",
    login_page: bool = False,
    fields: list[FakeField] | None = None,
    actions: list[FakeAction] | None = None,
    signals: list[dict[str, str]] | None = None,
    body_text: str = "",
    heading: str = "",
    links: list[dict[str, str]] | None = None,
) -> FakePage:
    return FakePage(
        path=path,
        title=title,
        login_page=login_page,
        fields=list(fields or []),
        actions=list(actions or []),
        signals=list(signals or []),
        body_text=body_text,
        heading=heading,
        links=list(links or []),
    )


def standard_companion(
    *,
    reason_value: str = "",
    signal_high_risk: bool = False,
    unknown_field: bool = False,
    extra_page: FakePage | None = None,
) -> FakeCompanion:
    """A low-risk mock transaction with an app list, login page and form."""

    portal = make_page(
        TEST_DISCOVERY_PATH,
        title="网上办事大厅",
        heading="可用事项",
        actions=[
            FakeAction("act:0", "在读证明申请", kind="other"),
            FakeAction("act:1", "研究生成绩单打印", kind="other"),
        ],
    )
    login = make_page(TEST_LOGIN_PATH, title="统一身份认证", login_page=True)
    fields = [
        FakeField(
            "ctl:0:0",
            value=reason_value,
            name="reason",
            required=True,
            max_length=200,
        ),
        FakeField(
            "ctl:0:1",
            value="",
            name="phone",
            required=True,
            max_length=20,
            tag="input",
        ),
        FakeField(
            "ctl:1:0",
            kind="select",
            value="paper",
            name="delivery",
            required=True,
            options=("paper", "email"),
            tag="select",
        ),
    ]
    if unknown_field:
        fields.append(FakeField("ctl:0:2", name="mystery", required=False))
    actions = [FakeAction("act:0", "提交申请", "submit")]
    signals: list[dict[str, str]] = []
    if signal_high_risk:
        signals.append(
            {
                "code": "PROHIBITED_TERM",
                "detail": "PAYMENT",
                "risk": RiskLevel.PROHIBITED.value,
            }
        )
    app = make_page(
        TEST_APP_PATH,
        title="在读证明申请",
        heading="在读证明申请",
        fields=fields,
        actions=actions,
        signals=signals,
    )
    transcript = make_page(
        TEST_TRANSCRIPT_PATH,
        title="研究生成绩单打印",
        heading="在读证明申请",
        fields=[
            FakeField(
                "ctl:0:0", value="", name="reason", required=True, max_length=200
            ),
            FakeField(
                "ctl:0:1",
                value="",
                name="phone",
                required=True,
                max_length=20,
                tag="input",
            ),
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
        actions=[FakeAction("act:0", "提交申请", "submit")],
    )
    pages = {
        TEST_DISCOVERY_PATH: portal,
        TEST_LOGIN_PATH: login,
        TEST_APP_PATH: app,
        TEST_TRANSCRIPT_PATH: transcript,
    }
    if extra_page is not None:
        pages[extra_page.path] = extra_page
    return FakeCompanion(pages=pages)


def standard_adapter(**overrides: Any) -> TransactionAdapterDescriptor:
    values: dict[str, Any] = {
        "extension_id": "nju.ehall",
        "extension_version": "0.1.0",
        "adapter_id": "nju.ehall.proof",
        "adapter_version": "1.0.0",
        "display_name": "在读证明申请",
        "allowed_origins": (TEST_ORIGIN,),
        "allowed_paths": (TEST_APP_PATH,),
        "declared_risk": RiskLevel.EXTERNAL_WRITE,
        "transaction_ids": ("proof.apply",),
        "fields": (
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
                pattern=r"1[0-9]{10}",
                locator="ctl:0:1",
            ),
            AdapterFieldSpec(
                field_id="delivery",
                label="领取方式",
                kind="select",
                required=True,
                locator="ctl:1:0",
            ),
        ),
        "actions": (
            AdapterActionSpec(
                action_id="proof.submit",
                locator="act:0",
                label="提交申请",
                kind="submit",
                risk=RiskLevel.EXTERNAL_WRITE,
                final=True,
                method="POST",
                target_origin=TEST_ORIGIN,
                target_path=TEST_SUBMIT_PATH,
            ),
            AdapterActionSpec(
                action_id="proof.open",
                locator="act:0",
                label="在读证明申请",
                kind="navigate",
                risk=RiskLevel.INTERNAL_WRITE,
                transaction_id="proof.apply",
                navigates_to_path=TEST_APP_PATH,
            ),
        ),
        "login_paths": (TEST_LOGIN_PATH,),
        "discovery_path": TEST_DISCOVERY_PATH,
        "tracking_path": TEST_TRACKING_PATH,
        "receipt_locator": "text:回执号",
        "receipt_pattern": r"NJU-2026-[0-9]{4}",
        "allowed_page_fingerprints": (app_page_fingerprint(),),
        "consequences": "提交后进入院系审核，材料不实将影响办理。",
    }
    values.update(overrides)
    return TransactionAdapterDescriptor(**values)


def transcript_adapter(**overrides: Any) -> TransactionAdapterDescriptor:
    values: dict[str, Any] = {
        "extension_id": "nju.ehall",
        "extension_version": "0.1.0",
        "adapter_id": "nju.ehall.transcript",
        "adapter_version": "1.0.0",
        "display_name": "研究生成绩单打印",
        "allowed_origins": (TEST_ORIGIN,),
        "allowed_paths": (TEST_TRANSCRIPT_PATH,),
        "declared_risk": RiskLevel.EXTERNAL_WRITE,
        "transaction_ids": ("transcript.apply",),
        "fields": standard_adapter().fields,
        "actions": (
            AdapterActionSpec(
                action_id="transcript.submit",
                locator="act:0",
                label="提交申请",
                kind="submit",
                risk=RiskLevel.EXTERNAL_WRITE,
                final=True,
                method="POST",
                target_origin=TEST_ORIGIN,
                target_path=TEST_TRANSCRIPT_SUBMIT_PATH,
            ),
            AdapterActionSpec(
                action_id="transcript.open",
                locator="act:1",
                label="研究生成绩单打印",
                kind="navigate",
                risk=RiskLevel.INTERNAL_WRITE,
                transaction_id="transcript.apply",
                navigates_to_path=TEST_TRANSCRIPT_PATH,
            ),
        ),
        "login_paths": (TEST_LOGIN_PATH,),
        "discovery_path": TEST_DISCOVERY_PATH,
        "tracking_path": TEST_TRACKING_PATH,
        "receipt_locator": "text:回执号",
        "receipt_pattern": r"NJU-2026-[0-9]{4}",
        "allowed_page_fingerprints": (app_page_fingerprint(),),
        "consequences": "提交后生成成绩单打印申请。",
    }
    values.update(overrides)
    return TransactionAdapterDescriptor(**values)


def standard_plan(
    *,
    reason_old: str = "",
    reason_new: str = "需要办理在读证明",
    phone_old: str = "",
    phone_new: str = "13800000000",
    delivery_old: str = "paper",
    delivery_new: str = "paper",
    expected_fingerprint: str = "",
    expected_origin: str = TEST_ORIGIN,
    attachments: tuple[Any, ...] = (),
) -> FillPlan:
    return FillPlan(
        adapter_id="nju.ehall.proof",
        adapter_version="1.0.0",
        transaction_id="proof.apply",
        expected_origin=expected_origin,
        expected_page_fingerprint=expected_fingerprint,
        app_id="proof",
        consequences="提交后进入院系审核，材料不实将影响办理。",
        attachments=attachments,
        fields=(
            FieldChange(
                field_id="reason",
                locator="ctl:0:0",
                label="申请理由",
                old_value=reason_old,
                new_value=reason_new,
                source=FieldValueSource.USER_INPUT,
            ),
            FieldChange(
                field_id="phone",
                locator="ctl:0:1",
                label="联系电话",
                old_value=phone_old,
                new_value=phone_new,
                source=FieldValueSource.USER_INPUT,
            ),
            FieldChange(
                field_id="delivery",
                locator="ctl:1:0",
                label="领取方式",
                old_value=delivery_old,
                new_value=delivery_new,
                source=FieldValueSource.USER_INPUT,
            ),
        ),
    )


__all__ = [
    "TEST_APP_PATH",
    "TEST_DISCOVERY_PATH",
    "TEST_LOGIN_PATH",
    "TEST_ORIGIN",
    "TEST_SUBMIT_PATH",
    "TEST_TRANSCRIPT_PATH",
    "TEST_TRANSCRIPT_SUBMIT_PATH",
    "app_page_fingerprint",
    "transcript_adapter",
    "TEST_TRACKING_PATH",
    "FakeAction",
    "FakeCompanion",
    "FakeField",
    "FakePage",
    "make_page",
    "standard_adapter",
    "standard_companion",
    "standard_plan",
]
