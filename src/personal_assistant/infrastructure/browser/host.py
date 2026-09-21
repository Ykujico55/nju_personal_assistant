"""Read-only ``host.browser.*`` capability exposed to extension workers.

The host owns the visible Desktop Companion session and all browser mechanics.
An extension may create a supervised session, read bounded structured snapshots
and record workflow state.  Typing and clicking are deliberately absent: those
are host executor actions behind an R2 approval.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from personal_assistant.core.browser import (
    BrowserAdapterRecord,
    BrowserError,
    BrowserSessionBroker,
    BrowserSessionRecord,
    BrowserSessionState,
    PageSnapshot,
    RiskAssessment,
    TransactionAdapterDescriptor,
    descriptor_from_document,
)
from personal_assistant.core.extensions.errors import ExtensionOperationError

HOST_BROWSER_SESSION = "host.browser.session"
HOST_BROWSER_STATUS = "host.browser.status"
HOST_BROWSER_REGISTER_ADAPTER = "host.browser.register_adapter"
HOST_BROWSER_ADAPTERS = "host.browser.adapters"
HOST_BROWSER_SNAPSHOT = "host.browser.snapshot"
HOST_BROWSER_FIND_TEXT = "host.browser.find_text"
HOST_BROWSER_NAVIGATE = "host.browser.navigate"
HOST_BROWSER_CLASSIFY_LABELS = "host.browser.classify_labels"
HOST_BROWSER_RECORD_DISCOVERY = "host.browser.record_discovery"
HOST_BROWSER_RECORD_PREPARATION = "host.browser.record_preparation"
HOST_BROWSER_RECONCILE = "host.browser.reconcile"
HOST_BROWSER_CLOSE = "host.browser.close"
HOST_BROWSER_CANCEL = "host.browser.cancel"

HOST_BROWSER_METHODS = frozenset(
    {
        HOST_BROWSER_SESSION,
        HOST_BROWSER_STATUS,
        HOST_BROWSER_REGISTER_ADAPTER,
        HOST_BROWSER_ADAPTERS,
        HOST_BROWSER_SNAPSHOT,
        HOST_BROWSER_FIND_TEXT,
        HOST_BROWSER_NAVIGATE,
        HOST_BROWSER_CLASSIFY_LABELS,
        HOST_BROWSER_RECORD_DISCOVERY,
        HOST_BROWSER_RECORD_PREPARATION,
        HOST_BROWSER_RECONCILE,
        HOST_BROWSER_CLOSE,
        HOST_BROWSER_CANCEL,
    }
)

MAX_LABELS = 64


class BrowserCapabilityError(ExtensionOperationError):
    pass


@dataclass(frozen=True, slots=True)
class BrowserCapabilityContext:
    extension_id: str
    extension_version: str


class BrowserHostCapability:
    def __init__(self, broker: BrowserSessionBroker) -> None:
        self._broker = broker

    @property
    def available(self) -> bool:
        return True

    async def handle(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        context: BrowserCapabilityContext,
    ) -> Any:
        if method not in HOST_BROWSER_METHODS:
            raise BrowserCapabilityError(
                "BROWSER_PROTOCOL_ERROR", f"unknown browser method: {method}"
            )
        try:
            return await self._dispatch(method, params, context)
        except BrowserCapabilityError:
            raise
        except BrowserError as exc:
            code = getattr(exc, "reason", "") or exc.code
            raise BrowserCapabilityError(code, str(exc)) from None
        except Exception as exc:  # pragma: no cover - defensive
            raise BrowserCapabilityError(
                "BROWSER_PROTOCOL_ERROR", "browser capability failed"
            ) from exc

    async def _dispatch(
        self,
        method: str,
        params: Mapping[str, Any],
        context: BrowserCapabilityContext,
    ) -> Any:
        extension_id = context.extension_id
        if method == HOST_BROWSER_SESSION:
            record = await self._broker.create_session(
                task_id=_text(params.get("task_id")),
                extension_id=extension_id,
                extension_version=context.extension_version,
                purpose=_text(params.get("purpose")),
            )
            return _session_view(record)
        if method == HOST_BROWSER_REGISTER_ADAPTER:
            descriptor = _descriptor(params.get("descriptor"), context)
            adapter = await self._broker.register_adapter(descriptor)
            return _adapter_view(adapter)
        if method == HOST_BROWSER_ADAPTERS:
            records = await self._broker.list_adapters(extension_id)
            return {"adapters": [_adapter_view(item) for item in records]}
        if method == HOST_BROWSER_CLASSIFY_LABELS:
            labels = params.get("labels")
            if not isinstance(labels, Sequence) or isinstance(labels, (str, bytes)):
                raise BrowserCapabilityError("BROWSER_PROTOCOL_ERROR", "labels must be a list")
            if len(labels) > MAX_LABELS:
                raise BrowserCapabilityError("BROWSER_LIMIT_EXCEEDED", "too many labels")
            terms = params.get("forbidden_terms")
            forbidden = (
                tuple(str(item) for item in terms)
                if isinstance(terms, Sequence) and not isinstance(terms, (str, bytes))
                else ()
            )
            classified = await self._broker.classify_labels(
                [str(item) for item in labels], forbidden_terms=forbidden
            )
            return {
                "labels": [
                    {
                        "label": label,
                        "matched": [
                            {"category": match.category.value, "term": match.term}
                            for match in matches
                        ],
                    }
                    for label, matches in classified
                ]
            }
        session_id = _session_id(params)
        if method == HOST_BROWSER_STATUS:
            record, status = await self._broker.session_status(
                session_id, extension_id=extension_id
            )
            return {
                **_session_view(record),
                "runtime": {
                    "open": status.open,
                    "url": status.url,
                    "origin": status.origin,
                    "path": status.path,
                    "login_page": status.login_page,
                    "headless": status.headless,
                    "browser_alive": status.browser_alive,
                    "blocked_origin_requests": status.blocked_origin_requests,
                    "blocked_mutating_requests": status.blocked_mutating_requests,
                    "fill_operations": status.fill_operations,
                    "click_operations": status.click_operations,
                },
            }
        if method == HOST_BROWSER_SNAPSHOT:
            snapshot, assessment = await self._broker.snapshot(
                session_id,
                extension_id=extension_id,
                adapter_id=_optional_text(params, "adapter_id"),
                transaction_id=_optional_text(params, "transaction_id"),
            )
            return _snapshot_view(snapshot, assessment)
        if method == HOST_BROWSER_FIND_TEXT:
            return await self._broker.find_text(
                session_id,
                extension_id=extension_id,
                query=_text(params.get("query"), max_length=200),
            )
        if method == HOST_BROWSER_NAVIGATE:
            snapshot = await self._broker.navigate(
                session_id,
                extension_id=extension_id,
                adapter_id=_text(params.get("adapter_id")),
                transaction_id=_text(params.get("transaction_id")),
                url=_text(params.get("url")),
            )
            return _snapshot_view(snapshot, None)
        if method == HOST_BROWSER_RECORD_DISCOVERY:
            record = await self._broker.record_discovery(
                session_id,
                extension_id=extension_id,
                app_count=_int(params.get("app_count")),
            )
            return _session_view(record)
        if method == HOST_BROWSER_RECORD_PREPARATION:
            record = await self._broker.record_preparation(
                session_id,
                extension_id=extension_id,
                adapter_id=_text(params.get("adapter_id")),
                adapter_version=_text(params.get("adapter_version")),
                app_id=_text(params.get("app_id")),
                transaction_id=_text(params.get("transaction_id")),
                page_fingerprint=_text(params.get("page_fingerprint")),
                planned_fields=_int(params.get("planned_fields")),
            )
            return _session_view(record)
        if method == HOST_BROWSER_RECONCILE:
            # The host performs the read-only tracking query and issues the
            # proof; the caller cannot declare a MATCHED result.
            record = await self._broker.reconcile(session_id, extension_id=extension_id)
            return _session_view(record)
        if method == HOST_BROWSER_CLOSE:
            await self._broker.close_session(session_id, extension_id=extension_id)
            return {"closed": True}
        if method == HOST_BROWSER_CANCEL:
            await self._broker.cancel_session(session_id, extension_id=extension_id)
            return {"cancelled": True}
        raise BrowserCapabilityError("BROWSER_PROTOCOL_ERROR", f"unknown browser method: {method}")



def _text(value: Any, *, max_length: int = 2048) -> str:
    if not isinstance(value, str):
        raise BrowserCapabilityError("BROWSER_PROTOCOL_ERROR", "a text parameter is malformed")
    if len(value) > max_length:
        raise BrowserCapabilityError("BROWSER_LIMIT_EXCEEDED", "a text parameter is too long")
    return value


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BrowserCapabilityError("BROWSER_PROTOCOL_ERROR", "an integer parameter is malformed")
    return int(value)


def _optional_text(params: Mapping[str, Any], key: str) -> str:
    value = params.get(key, "")
    return _text(value if value is not None else "", max_length=2048)


def _session_id(params: Mapping[str, Any]) -> str:
    session_id = _text(params.get("session_id"), max_length=200)
    if not session_id:
        raise BrowserCapabilityError("BROWSER_PROTOCOL_ERROR", "session_id is required")
    return session_id


def _descriptor(
    value: Any, context: BrowserCapabilityContext
) -> TransactionAdapterDescriptor:
    if not isinstance(value, Mapping):
        raise BrowserCapabilityError(
            "BROWSER_PROTOCOL_ERROR", "the adapter descriptor is malformed"
        )
    try:
        descriptor = descriptor_from_document(value)
    except (KeyError, ValueError, TypeError) as exc:
        raise BrowserCapabilityError(
            "BROWSER_PROTOCOL_ERROR", "the adapter descriptor is malformed"
        ) from exc
    if descriptor.extension_id != context.extension_id:
        raise BrowserCapabilityError(
            "BROWSER_PROTOCOL_ERROR", "the adapter must belong to the calling extension"
        )
    if descriptor.extension_version != context.extension_version:
        raise BrowserCapabilityError(
            "BROWSER_PROTOCOL_ERROR", "the adapter version must match the installed extension"
        )
    return descriptor


def _session_view(record: BrowserSessionRecord) -> Mapping[str, Any]:
    return {
        "session_id": record.session_id,
        "task_id": record.task_id,
        "state": record.state.value,
        "purpose": record.purpose,
        "origin": record.origin,
        "url": record.url,
        "adapter_id": record.adapter_id,
        "adapter_version": record.adapter_version,
        "app_id": record.app_id,
        "transaction_id": record.transaction_id,
        "page_fingerprint": record.page_fingerprint,
        "preview_hash": record.preview_hash,
        "has_preview": record.preview is not None,
        "outcome": record.outcome,
        "receipt": dict(record.receipt) if record.receipt else None,
        "diagnostic_code": record.diagnostic_code,
        "re_navigations": record.re_navigations,
        "fill_count": record.fill_count,
        "submit_count": record.submit_count,
        "apps_count": record.apps_count,
        "user_action_required": record.state is BrowserSessionState.WAITING_USER,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
    }


def _adapter_view(record: BrowserAdapterRecord) -> Mapping[str, Any]:
    document = dict(record.descriptor)
    return {
        "adapter_id": record.adapter_id,
        "adapter_version": record.adapter_version,
        "extension_id": record.extension_id,
        "extension_version": record.extension_version,
        "descriptor": document,
        "updated_at": _iso(record.updated_at),
    }


def _snapshot_view(
    snapshot: PageSnapshot, assessment: RiskAssessment | None
) -> Mapping[str, Any]:
    return {
        "session_id": snapshot.session_id,
        "url": snapshot.url,
        "origin": snapshot.origin,
        "path": snapshot.path,
        "title": snapshot.title,
        "fingerprint": snapshot.fingerprint,
        "captured_at": _iso(snapshot.captured_at),
        "authenticated": snapshot.authenticated,
        "fields": [
            {
                "field_id": item.field_id,
                "locator": item.locator,
                "kind": item.kind,
                "value": item.value,
                "name": item.name,
                "required": item.required,
                "readonly": item.readonly,
                "options": list(item.options),
                "max_length": item.max_length,
                "known": item.known,
            }
            for item in snapshot.fields
        ],
        "actions": [
            {
                "action_id": item.action_id,
                "locator": item.locator,
                "label": item.label,
                "kind": item.kind,
                "risk": item.risk.value,
                "known": item.known,
            }
            for item in snapshot.actions
        ],
        "signals": [
            {"code": item.code, "detail": item.detail, "risk": item.risk.value}
            for item in snapshot.signals
        ],
        "links": [{"text": item.text, "path": item.path} for item in snapshot.links],
        "text_digest": snapshot.text_digest,
        "byte_size": snapshot.byte_size,
        "truncated": snapshot.truncated,
        "scan_incomplete": snapshot.scan_incomplete,
        "risk": assessment.risk.value if assessment else None,
        "risk_categories": [item.value for item in assessment.categories] if assessment else [],
        "escalated": assessment.escalated if assessment else False,
    }


def _iso(value: datetime) -> str:
    return value.isoformat()


__all__ = [
    "HOST_BROWSER_ADAPTERS",
    "HOST_BROWSER_CANCEL",
    "HOST_BROWSER_CLASSIFY_LABELS",
    "HOST_BROWSER_CLOSE",
    "HOST_BROWSER_FIND_TEXT",
    "HOST_BROWSER_METHODS",
    "HOST_BROWSER_NAVIGATE",
    "HOST_BROWSER_RECORD_DISCOVERY",
    "HOST_BROWSER_RECORD_PREPARATION",
    "HOST_BROWSER_RECONCILE",
    "HOST_BROWSER_REGISTER_ADAPTER",
    "HOST_BROWSER_SESSION",
    "HOST_BROWSER_SNAPSHOT",
    "HOST_BROWSER_STATUS",
    "BrowserCapabilityContext",
    "BrowserCapabilityError",
    "BrowserHostCapability",
]
