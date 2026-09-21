"""Ports and records for supervised browser sessions.

The Desktop Companion adapter implements :class:`DesktopBrowserPort`; it is the
only component allowed to import Playwright or a platform browser API.  Core
code works with the opaque values defined here and never sees a cookie, a
storage state, a password, a screenshot or unbounded HTML.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from personal_assistant.core.browser.errors import BrowserPolicyError
from personal_assistant.core.browser.models import (
    MAX_ACTIONS,
    MAX_FIELD_VALUE_CHARS,
    MAX_FIELDS,
    BrowserSessionState,
    FieldValueSource,
)
from personal_assistant.core.browser.policy import normalize_origin
from personal_assistant.domain.enums import RiskLevel

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
#: Adapter locators are opaque index references, never raw CSS: the Desktop
#: Companion resolves them against the bounded, fingerprinted control list.
#: ``ctl:<group>:<index>`` where group 0=input, 1=select, 2=textarea.
_FIELD_LOCATOR_PATTERN = re.compile(r"^ctl:[0-2]:\d{1,4}$")
_ACTION_LOCATOR_PATTERN = re.compile(r"^act:\d{1,4}$")
_RECEIPT_LOCATOR_PATTERN = re.compile(
    r"^(?:text:.{1,200}|ctl:[0-2]:\d{1,4}|act:\d{1,4})$"
)
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SUBMIT_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_FIELD_KINDS = frozenset(
    {
        "text",
        "textarea",
        "select",
        "checkbox",
        "radio",
        "date",
        "number",
        "email",
        "tel",
        "file",
        "unknown",
    }
)
_ACTION_KINDS = frozenset({"submit", "next", "save", "other", "navigate"})


def _require_id(value: str, field_name: str, *, max_length: int = 128) -> None:
    if not isinstance(value, str) or not value:
        raise BrowserPolicyError("ADAPTER_INVALID", f"{field_name} is required")
    if len(value) > max_length or not _ID_PATTERN.match(value):
        raise BrowserPolicyError("ADAPTER_INVALID", f"{field_name} is malformed")


@dataclass(frozen=True, slots=True)
class AdapterFieldSpec:
    field_id: str
    label: str
    kind: str = "text"
    required: bool = True
    max_length: int = 0
    pattern: str = ""
    locator: str = ""
    source: FieldValueSource = FieldValueSource.USER_INPUT


@dataclass(frozen=True, slots=True)
class AdapterActionSpec:
    action_id: str
    locator: str
    label: str
    kind: str = "submit"
    risk: RiskLevel = RiskLevel.EXTERNAL_WRITE
    final: bool = False
    #: Bound network target of a final submission: exactly one matching write
    #: request is allowed once the action is approved.
    method: str = ""
    target_origin: str = ""
    target_path: str = ""
    #: A ``navigate`` action opens one declared transaction: the host verifies
    #: the landing page path and its pinned fingerprint before any fill.
    transaction_id: str = ""
    navigates_to_path: str = ""


@dataclass(frozen=True, slots=True)
class TransactionAdapterDescriptor:
    """Everything the host needs to execute one versioned transaction.

    It contains no credentials and no business branch in core code: the host
    treats every value as opaque data and validates it structurally.
    """

    extension_id: str
    extension_version: str
    adapter_id: str
    adapter_version: str
    display_name: str
    allowed_origins: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    declared_risk: RiskLevel
    transaction_ids: tuple[str, ...]
    fields: tuple[AdapterFieldSpec, ...] = ()
    actions: tuple[AdapterActionSpec, ...] = ()
    login_paths: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    discovery_path: str = ""
    tracking_path: str = ""
    receipt_locator: str = ""
    consequences: str = ""
    #: Human-verified page versions.  A live page whose fingerprint is not in
    #: this set is ``UNKNOWN_PAGE_VERSION``/R3 and is never filled or clicked.
    allowed_page_fingerprints: tuple[str, ...] = ()
    #: Optional regex the extracted receipt reference must match.
    receipt_pattern: str = ""

    def final_action(self) -> AdapterActionSpec | None:
        for action in self.actions:
            if action.final:
                return action
        return None

    def field_spec(self, field_id: str) -> AdapterFieldSpec | None:
        for spec in self.fields:
            if spec.field_id == field_id:
                return spec
        return None

    def field_spec_by_locator(self, locator: str) -> AdapterFieldSpec | None:
        for spec in self.fields:
            if spec.locator == locator:
                return spec
        return None

    def action_by_locator(self, locator: str) -> AdapterActionSpec | None:
        for action in self.actions:
            if action.locator == locator:
                return action
        return None

    def navigation_action(self, transaction_id: str) -> AdapterActionSpec | None:
        for action in self.actions:
            if action.kind == "navigate" and action.transaction_id == transaction_id:
                return action
        return None


def validate_adapter_descriptor(
    descriptor: TransactionAdapterDescriptor,
    *,
    allowed_origins: frozenset[str] | set[str],
) -> None:
    """Structural validation; raises ``BrowserPolicyError`` on any violation."""

    _require_id(descriptor.extension_id, "extension_id")
    _require_id(descriptor.adapter_id, "adapter_id")
    if not descriptor.extension_version or len(descriptor.extension_version) > 64:
        raise BrowserPolicyError("ADAPTER_INVALID", "extension_version is required")
    if not descriptor.adapter_version or len(descriptor.adapter_version) > 64:
        raise BrowserPolicyError("ADAPTER_INVALID", "adapter_version is required")
    if not descriptor.display_name or len(descriptor.display_name) > 200:
        raise BrowserPolicyError("ADAPTER_INVALID", "display_name is required")
    if not descriptor.allowed_origins or len(descriptor.allowed_origins) > 8:
        raise BrowserPolicyError("ADAPTER_INVALID", "one to eight adapter origins are required")
    if len(descriptor.allowed_paths) > 64:
        raise BrowserPolicyError("ADAPTER_INVALID", "too many allowed paths")
    if not descriptor.transaction_ids or len(descriptor.transaction_ids) > 64:
        raise BrowserPolicyError("ADAPTER_INVALID", "transaction ids are required")
    if descriptor.declared_risk is RiskLevel.PROHIBITED:
        raise BrowserPolicyError(
            "ADAPTER_RISK_PROHIBITED", "a prohibited transaction can never be declared"
        )
    for origin in descriptor.allowed_origins:
        canonical = normalize_origin(origin)
        if canonical != origin:
            raise BrowserPolicyError("ADAPTER_INVALID", "adapter origins must be normalized")
        if canonical not in allowed_origins:
            raise BrowserPolicyError(
                "ORIGIN_NOT_ALLOWED",
                "an adapter origin is not in the user allowlist",
            )
    for path in (*descriptor.allowed_paths, *descriptor.login_paths):
        _validate_path(path)
    if descriptor.discovery_path:
        _validate_path(descriptor.discovery_path)
    if descriptor.tracking_path:
        _validate_path(descriptor.tracking_path)
    if descriptor.receipt_locator and not _RECEIPT_LOCATOR_PATTERN.match(
        descriptor.receipt_locator
    ):
        raise BrowserPolicyError(
            "ADAPTER_INVALID", "receipt locator must be a bounded text/ctl/act reference"
        )
    if descriptor.receipt_pattern:
        if len(descriptor.receipt_pattern) > 256:
            raise BrowserPolicyError("ADAPTER_INVALID", "receipt pattern is too long")
        try:
            re.compile(descriptor.receipt_pattern)
        except re.error as exc:
            raise BrowserPolicyError(
                "ADAPTER_INVALID", "receipt pattern is not a valid regex"
            ) from exc
    if not descriptor.allowed_page_fingerprints:
        raise BrowserPolicyError(
            "ADAPTER_INVALID",
            "an adapter must carry at least one human-verified page fingerprint",
        )
    if len(descriptor.allowed_page_fingerprints) > 8:
        raise BrowserPolicyError("ADAPTER_INVALID", "too many allowed page fingerprints")
    for fingerprint in descriptor.allowed_page_fingerprints:
        if not _SHA256_HEX.match(fingerprint):
            raise BrowserPolicyError(
                "ADAPTER_INVALID", "allowed page fingerprints must be 64 lowercase hex"
            )
    if len(descriptor.consequences) > 2000:
        raise BrowserPolicyError("ADAPTER_INVALID", "consequences text is too long")
    if len(descriptor.fields) > MAX_FIELDS:
        raise BrowserPolicyError("ADAPTER_INVALID", "too many adapter fields")
    seen_fields: set[str] = set()
    for spec in descriptor.fields:
        if spec.field_id in seen_fields:
            raise BrowserPolicyError("ADAPTER_INVALID", "duplicate adapter field id")
        seen_fields.add(spec.field_id)
        _require_id(spec.field_id, "field_id")
        if spec.kind not in _FIELD_KINDS:
            raise BrowserPolicyError("ADAPTER_INVALID", f"unknown field kind {spec.kind!r}")
        if not _FIELD_LOCATOR_PATTERN.match(spec.locator):
            raise BrowserPolicyError(
                "ADAPTER_INVALID", "a field locator must be a ctl:<index> reference"
            )
        if not isinstance(spec.required, bool):
            raise BrowserPolicyError("ADAPTER_INVALID", "field required flag must be boolean")
        if spec.max_length < 0 or spec.max_length > MAX_FIELD_VALUE_CHARS:
            raise BrowserPolicyError("ADAPTER_INVALID", "field length limit is out of range")
        if spec.pattern:
            if len(spec.pattern) > 512:
                raise BrowserPolicyError("ADAPTER_INVALID", "field pattern is too long")
            try:
                re.compile(spec.pattern)
            except re.error as exc:
                raise BrowserPolicyError(
                    "ADAPTER_INVALID", "field pattern is not a valid regex"
                ) from exc
        if not isinstance(spec.source, FieldValueSource):
            raise BrowserPolicyError("ADAPTER_INVALID", "field source must be typed")
    if len(descriptor.actions) > MAX_ACTIONS:
        raise BrowserPolicyError("ADAPTER_INVALID", "too many adapter actions")
    seen_actions: set[str] = set()
    final_actions = 0
    for action in descriptor.actions:
        if action.action_id in seen_actions:
            raise BrowserPolicyError("ADAPTER_INVALID", "duplicate adapter action id")
        seen_actions.add(action.action_id)
        _require_id(action.action_id, "action_id")
        if action.kind not in _ACTION_KINDS:
            raise BrowserPolicyError("ADAPTER_INVALID", f"unknown action kind {action.kind!r}")
        if action.risk is RiskLevel.PROHIBITED:
            raise BrowserPolicyError(
                "ADAPTER_RISK_PROHIBITED", "a prohibited action can never be declared"
            )
        if not _ACTION_LOCATOR_PATTERN.match(action.locator):
            raise BrowserPolicyError(
                "ADAPTER_INVALID", "an action locator must be an act:<index> reference"
            )
        if action.kind == "navigate":
            if action.final:
                raise BrowserPolicyError(
                    "ADAPTER_INVALID", "a navigation action can never be final"
                )
            if action.method or action.target_origin or action.target_path:
                raise BrowserPolicyError(
                    "ADAPTER_INVALID",
                    "a navigation action must not declare a submit target",
                )
            if (
                not action.transaction_id
                or action.transaction_id not in descriptor.transaction_ids
            ):
                raise BrowserPolicyError(
                    "ADAPTER_INVALID",
                    "a navigation action must open a declared transaction",
                )
            _validate_navigation_path(action.navigates_to_path)
        elif action.transaction_id or action.navigates_to_path:
            raise BrowserPolicyError(
                "ADAPTER_INVALID",
                "only a navigation action may declare an entry target",
            )
        if action.final:
            final_actions += 1
            if action.method.upper() not in _SUBMIT_METHODS:
                raise BrowserPolicyError(
                    "ADAPTER_INVALID",
                    "a final action must declare a non-GET submit method",
                )
            canonical_origin = normalize_origin(action.target_origin)
            if canonical_origin != action.target_origin:
                raise BrowserPolicyError(
                    "ADAPTER_INVALID", "a final action origin must be normalized"
                )
            if canonical_origin not in allowed_origins:
                raise BrowserPolicyError(
                    "ORIGIN_NOT_ALLOWED",
                    "a final action target origin is not in the user allowlist",
                )
            _validate_path(action.target_path)
    if final_actions > 1:
        raise BrowserPolicyError("ADAPTER_INVALID", "only one final action may be declared")


def _validate_navigation_path(path: str) -> None:
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 256:
        raise BrowserPolicyError(
            "ADAPTER_INVALID", "a navigation target must start with '/'"
        )
    if ".." in path or "\\" in path or "?" in path:
        raise BrowserPolicyError("ADAPTER_INVALID", "a navigation target must be static")


def _validate_path(path: str) -> None:
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 256:
        raise BrowserPolicyError("ADAPTER_INVALID", "adapter paths must start with '/'")
    if ".." in path or "\\" in path or "?" in path or "#" in path:
        raise BrowserPolicyError("ADAPTER_INVALID", "adapter paths must be static")


@dataclass(frozen=True, slots=True)
class BrowserAdapterRecord:
    extension_id: str
    extension_version: str
    adapter_id: str
    adapter_version: str
    descriptor: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime
    version: int = 0


@dataclass(frozen=True, slots=True)
class BrowserSessionRecord:
    session_id: str
    task_id: str
    extension_id: str
    extension_version: str
    purpose: str
    state: BrowserSessionState
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    version: int = 0
    origin: str = ""
    url: str = ""
    adapter_id: str = ""
    adapter_version: str = ""
    app_id: str = ""
    transaction_id: str = ""
    page_fingerprint: str = ""
    preview_hash: str = ""
    preview_nonce: str = ""
    preview: Mapping[str, Any] | None = None
    outcome: str = ""
    receipt: Mapping[str, Any] | None = None
    diagnostic_code: str = ""
    re_navigations: int = 0
    visited_paths: tuple[str, ...] = ()
    receipt_baseline: str = ""
    fill_count: int = 0
    submit_count: int = 0
    apps_count: int = 0
    owner_id: str = ""

    @property
    def terminal(self) -> bool:
        return self.state.terminal


@dataclass(frozen=True, slots=True)
class CompanionSessionStatus:
    session_id: str
    open: bool
    url: str
    origin: str
    path: str
    login_page: bool
    headless: bool
    browser_alive: bool
    re_navigations: int
    blocked_origin_requests: int
    blocked_mutating_requests: int
    fill_operations: int
    click_operations: int


@dataclass(frozen=True, slots=True)
class CompanionClickResult:
    clicked: bool
    outcome: str
    url: str
    receipt_found: bool = False
    receipt_text: str = ""
    detail: str = ""


@runtime_checkable
class DesktopBrowserPort(Protocol):
    """Loopback transport to the Desktop Companion.

    Implementations must never return cookies, storage state, passwords,
    verification codes, screenshots or raw HTML to the host.
    """

    async def create_session(
        self,
        *,
        session_id: str,
        purpose: str,
        allowed_origins: tuple[str, ...],
        task_id: str,
        extension_id: str,
    ) -> Mapping[str, Any]: ...

    async def close_session(self, session_id: str) -> None: ...

    async def cancel_session(self, session_id: str) -> None: ...

    async def status(self, session_id: str) -> Mapping[str, Any]: ...

    async def navigate(
        self,
        session_id: str,
        url: str,
        *,
        login_paths: tuple[str, ...] = (),
    ) -> Mapping[str, Any]: ...

    async def snapshot(
        self,
        session_id: str,
        *,
        prohibited_terms: tuple[str, ...] = (),
        scan_text: bool = False,
        login_paths: tuple[str, ...] = (),
    ) -> Mapping[str, Any]: ...

    async def fill(
        self, session_id: str, fields: tuple[tuple[str, str], ...]
    ) -> Mapping[str, Any]: ...

    async def find_text(self, session_id: str, query: str) -> Mapping[str, Any]: ...

    async def activate(
        self, session_id: str, *, locator: str, expected_path: str
    ) -> Mapping[str, Any]: ...

    async def collect_matches(
        self, session_id: str, *, pattern: str, limit: int, url: str = ""
    ) -> Mapping[str, Any]: ...

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
    ) -> Mapping[str, Any]: ...

    async def aclose(self) -> None: ...


class BrowserSessionStore(Protocol):
    """Durable session bookkeeping; never stores cookies or credentials."""

    async def create(self, record: BrowserSessionRecord) -> BrowserSessionRecord: ...

    async def get(self, session_id: str) -> BrowserSessionRecord | None: ...

    async def save(
        self, record: BrowserSessionRecord, *, expected_version: int
    ) -> BrowserSessionRecord: ...

    async def find_active_for_task(self, task_id: str) -> tuple[BrowserSessionRecord, ...]: ...

    async def recover_stale_executions(
        self, *, active_owners: Sequence[str] = ()
    ) -> tuple[str, ...]: ...

    async def close(self) -> None: ...


class BrowserAdapterStore(Protocol):
    """Durable per-extension transaction adapter registry."""

    async def upsert(self, record: BrowserAdapterRecord) -> BrowserAdapterRecord: ...

    async def get(
        self, extension_id: str, adapter_id: str, adapter_version: str
    ) -> BrowserAdapterRecord | None: ...

    async def list_for_extension(
        self, extension_id: str
    ) -> tuple[BrowserAdapterRecord, ...]: ...

    async def close(self) -> None: ...


__all__ = [
    "AdapterActionSpec",
    "AdapterFieldSpec",
    "BrowserAdapterRecord",
    "BrowserAdapterStore",
    "BrowserSessionRecord",
    "BrowserSessionStore",
    "CompanionClickResult",
    "CompanionSessionStatus",
    "DesktopBrowserPort",
    "TransactionAdapterDescriptor",
    "validate_adapter_descriptor",
]
