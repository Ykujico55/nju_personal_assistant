"""Selectable origin policy and permanent R3 classification.

Policy rules (F07 contract):

* allowlist mode visits only explicit https origins; an explicitly selected
  open mode accepts any structurally valid https origin;
* userinfo, non-default ports that were not allow-listed, IP literals that were
  not allow-listed, encoded traversal, protocol-relative and open-redirect URLs
  are rejected;
* page text and labels can only *raise* a risk classification.  Withdrawal,
  revocation, payment, course change, legal declarations, unknown transactions
  and unknown page versions are permanently ``PROHIBITED``/R3 and can never be
  downgraded by a manifest flag or a user click.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import SplitResult, parse_qsl, unquote, urlsplit

from personal_assistant.core.browser.errors import BrowserPolicyError
from personal_assistant.domain.enums import RiskLevel

MAX_URL_LENGTH = 2048
MAX_SCAN_CHARS = 200_000

_REDIRECT_QUERY_KEYS = frozenset(
    {
        "url",
        "uri",
        "redirect",
        "redirect_uri",
        "redirect_url",
        "redirect_to",
        "next",
        "target",
        "continue",
        "callback",
        "return",
        "return_url",
        "returnurl",
        "goto",
        "dest",
        "destination",
        "forward",
        "service",
    }
)

_FORBIDDEN_SCHEMES = frozenset(
    {
        "http",
        "javascript",
        "data",
        "file",
        "blob",
        "about",
        "chrome",
        "chrome-extension",
        "vbscript",
        "ftp",
        "ws",
        "wss",
        "mailto",
        "tel",
        "view-source",
    }
)


def risk_rank(risk: RiskLevel) -> int:
    return {
        RiskLevel.READ: 0,
        RiskLevel.INTERNAL_WRITE: 1,
        RiskLevel.EXTERNAL_WRITE: 2,
        RiskLevel.PROHIBITED: 3,
    }[risk]


def escalate_risk(base: RiskLevel, observed: RiskLevel) -> RiskLevel:
    return base if risk_rank(base) >= risk_rank(observed) else observed


@dataclass(frozen=True, slots=True)
class OriginPolicy:
    allowed_origins: frozenset[str]
    allowed_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NavigationDecision:
    allowed: bool
    origin: str = ""
    path: str = ""
    reason: str = "ALLOWED"


def _origin_port(parts: SplitResult) -> int | None:
    try:
        return parts.port
    except ValueError as exc:
        raise BrowserPolicyError("MALFORMED_ORIGIN", "origin has an invalid port") from exc


def normalize_origin(raw: str) -> str:
    """Canonicalize ``https://host[:port]`` or raise ``BrowserPolicyError``."""

    if not isinstance(raw, str) or not raw.strip():
        raise BrowserPolicyError("EMPTY_ORIGIN", "origin is required")
    value = raw.strip()
    if value != raw:
        raise BrowserPolicyError("MALFORMED_ORIGIN", "origin must not contain padding")
    if any(ord(ch) < 0x21 for ch in value):
        raise BrowserPolicyError("CONTROL_CHARACTERS", "origin contains control characters")
    parts = urlsplit(value)
    if parts.scheme.lower() != "https":
        raise BrowserPolicyError("SCHEME_NOT_ALLOWED", "only https origins are allowed")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise BrowserPolicyError("MALFORMED_ORIGIN", "origin must not carry path/query/fragment")
    if "@" in parts.netloc:
        raise BrowserPolicyError("USERINFO_NOT_ALLOWED", "origin must not carry userinfo")
    host = parts.hostname
    if not host:
        raise BrowserPolicyError("MISSING_HOST", "origin requires a host")
    port = _origin_port(parts)
    if port == 0:
        raise BrowserPolicyError("MALFORMED_ORIGIN", "port zero is not a valid origin")
    canonical_host = _canonical_host(host)
    if port in (None, 443):
        return f"https://{canonical_host}"
    return f"https://{canonical_host}:{port}"


def _canonical_host(host: str) -> str:
    value = host.lower()
    while value.endswith("."):
        value = value[:-1]
    if not value:
        raise BrowserPolicyError("MISSING_HOST", "origin requires a host")
    if any(ch in value for ch in "%\\/"):
        raise BrowserPolicyError("MALFORMED_HOST", "origin host is malformed")
    return value


def _decoded_path(path: str) -> str:
    current = path
    for _ in range(3):
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded
    return current


def evaluate_navigation(
    url: str,
    *,
    allowed_origins: frozenset[str] | set[str],
    allowed_paths: Sequence[str] = (),
    origin_mode: str = "allowlist",
) -> NavigationDecision:
    """Decide whether the supervised browser may navigate to ``url``."""

    if origin_mode not in {"allowlist", "open"}:
        return NavigationDecision(False, reason="ORIGIN_MODE_INVALID")
    if not isinstance(url, str) or not url:
        return NavigationDecision(False, reason="EMPTY_URL")
    if len(url) > MAX_URL_LENGTH:
        return NavigationDecision(False, reason="URL_TOO_LONG")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return NavigationDecision(False, reason="CONTROL_CHARACTERS")
    if url != url.strip():
        return NavigationDecision(False, reason="MALFORMED_URL")
    if "\\" in url:
        return NavigationDecision(False, reason="BACKSLASH")
    try:
        parts = urlsplit(url)
    except ValueError:
        return NavigationDecision(False, reason="MALFORMED_URL")
    scheme = parts.scheme.lower()
    if scheme in _FORBIDDEN_SCHEMES or scheme != "https":
        return NavigationDecision(False, reason="SCHEME_NOT_ALLOWED")
    if "@" in parts.netloc:
        return NavigationDecision(False, reason="USERINFO_NOT_ALLOWED")
    host = parts.hostname
    if not host:
        return NavigationDecision(False, reason="MISSING_HOST")
    try:
        port = _origin_port(parts)
        canonical_origin = normalize_origin(
            f"https://{host}:{port}" if port else f"https://{host}"
        )
    except BrowserPolicyError:
        return NavigationDecision(False, reason="MALFORMED_URL")
    if port == 0:
        return NavigationDecision(False, reason="PORT_NOT_ALLOWED")
    if origin_mode == "allowlist" and canonical_origin not in allowed_origins:
        return NavigationDecision(False, canonical_origin, reason="HOST_NOT_ALLOWED")
    path = parts.path or "/"
    decoded = _decoded_path(path)
    if "\\" in decoded:
        return NavigationDecision(False, canonical_origin, path, "ENCODED_SEPARATOR")
    if "\x00" in decoded:
        return NavigationDecision(False, canonical_origin, path, "ENCODED_SEPARATOR")
    for segment in decoded.split("/"):
        if segment in (".", ".."):
            return NavigationDecision(False, canonical_origin, path, "ENCODED_TRAVERSAL")
    if _has_open_redirect(parts.query, allowed_origins, origin_mode=origin_mode):
        return NavigationDecision(False, canonical_origin, path, "OPEN_REDIRECT")
    if allowed_paths and not any(_path_matches(path, pattern) for pattern in allowed_paths):
        return NavigationDecision(False, canonical_origin, path, "PATH_NOT_ALLOWED")
    return NavigationDecision(True, canonical_origin, path, "ALLOWED")


def _path_matches(path: str, pattern: str) -> bool:
    from fnmatch import fnmatchcase

    return fnmatchcase(path, pattern)


def _has_open_redirect(
    query: str,
    allowed_origins: frozenset[str] | set[str],
    *,
    origin_mode: str = "allowlist",
) -> bool:
    """Reject redirect parameters that could send the browser off the allowlist.

    A redirect target is only accepted when it stays on an allowlisted https
    origin (this keeps standard CAS ``service=`` flows working); non-https
    schemes, userinfo/backslash tricks and off-allowlist hosts are rejected.
    """

    if not query:
        return False
    try:
        items = parse_qsl(query, keep_blank_values=True)
    except ValueError:
        return True
    for key, value in items:
        if key.lower() not in _REDIRECT_QUERY_KEYS:
            continue
        candidate = value.strip()
        if not candidate:
            continue
        # Decode repeatedly: a doubly-encoded target (``%253A%252F%252F``)
        # must not slip past the open-redirect checks.
        for _ in range(3):
            decoded = unquote(candidate)
            if decoded == candidate:
                break
            candidate = decoded
        if not _redirect_target_allowed(
            candidate, allowed_origins, origin_mode=origin_mode
        ):
            return True
    return False


def _redirect_target_allowed(
    candidate: str,
    allowed_origins: frozenset[str] | set[str],
    *,
    origin_mode: str = "allowlist",
) -> bool:
    if "\\" in candidate or "@" in candidate:
        return False
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    try:
        nested = urlsplit(candidate)
    except ValueError:
        return False
    if not nested.scheme:
        # A relative path stays on the current (already allowlisted) origin.
        return True
    if nested.scheme != "https" or not nested.hostname:
        return False
    try:
        origin = normalize_origin(f"{nested.scheme}://{nested.netloc}")
    except BrowserPolicyError:
        return False
    return origin_mode == "open" or origin in allowed_origins


class ProhibitedCategory(StrEnum):
    COURSE_WITHDRAWAL = "COURSE_WITHDRAWAL"
    REVOCATION = "REVOCATION"
    PAYMENT = "PAYMENT"
    COURSE_CHANGE = "COURSE_CHANGE"
    LEGAL_DECLARATION = "LEGAL_DECLARATION"
    HIGH_CONSEQUENCE = "HIGH_CONSEQUENCE"
    UNKNOWN_TRANSACTION = "UNKNOWN_TRANSACTION"
    UNKNOWN_PAGE_VERSION = "UNKNOWN_PAGE_VERSION"


#: Terms are matched case-insensitively for ASCII and as substrings for CJK.
PROHIBITED_TERM_CATALOG: tuple[tuple[ProhibitedCategory, tuple[str, ...]], ...] = (
    (
        ProhibitedCategory.COURSE_WITHDRAWAL,
        (
            "退课",
            "退选",
            "撤课",
            "取消选课",
            "withdraw course",
            "withdraw from course",
            "drop course",
            "course withdrawal",
            "course drop",
        ),
    ),
    (
        ProhibitedCategory.REVOCATION,
        (
            "撤销",
            "撤回",
            "撤销申请",
            "撤回申请",
            "取消申请",
            "revoke",
            "revocation",
            "withdraw application",
            "cancel application",
        ),
    ),
    (
        ProhibitedCategory.PAYMENT,
        (
            "缴费",
            "支付",
            "付款",
            "交费",
            "缴纳",
            "付费",
            "退费",
            "退款",
            "转账",
            "汇款",
            "网银",
            "payment",
            "pay fee",
            "make a payment",
            "checkout",
            "refund",
            "transfer funds",
        ),
    ),
    (
        ProhibitedCategory.COURSE_CHANGE,
        (
            "选课",
            "加课",
            "换课",
            "调课",
            "补选",
            "选课变更",
            "course registration",
            "add course",
            "drop/add",
            "change course",
        ),
    ),
    (
        ProhibitedCategory.LEGAL_DECLARATION,
        (
            "承诺书",
            "法律声明",
            "免责声明",
            "放弃权利",
            "授权书",
            "诚信承诺",
            "声明书",
            "责任免除",
            "legal declaration",
            "waiver",
            "disclaimer",
            "terms of service",
        ),
    ),
    (
        ProhibitedCategory.HIGH_CONSEQUENCE,
        (
            "违约金",
            "处罚",
            "处分",
            "冻结",
            "注销",
            "开除",
            "黑名单",
            "penalty",
            "blacklist",
            "termination",
            "consequences acknowledged",
        ),
    ),
)

_ASCII_TERMS: tuple[tuple[ProhibitedCategory, re.Pattern[str], str], ...] = tuple(
    (category, re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"), term)
    for category, terms in PROHIBITED_TERM_CATALOG
    for term in terms
    if term.isascii()
)

_CJK_TERMS: tuple[tuple[ProhibitedCategory, str], ...] = tuple(
    (category, term)
    for category, terms in PROHIBITED_TERM_CATALOG
    for term in terms
    if not term.isascii()
)


@dataclass(frozen=True, slots=True)
class ProhibitedMatch:
    category: ProhibitedCategory
    term: str
    span: tuple[int, int]


def scan_text_for_prohibited_terms(
    text: str,
    extra_terms: Sequence[str] = (),
    *,
    max_chars: int = MAX_SCAN_CHARS,
) -> tuple[ProhibitedMatch, ...]:
    """Find permanent-prohibition terms in untrusted page text.

    Only the matched term is returned; surrounding page text is never copied
    into the result, so callers cannot leak page content through signals.
    """

    if not isinstance(text, str) or not text:
        return ()
    bounded = text[:max_chars]
    matches: list[ProhibitedMatch] = []
    lowered = bounded.lower()
    for category, pattern, term in _ASCII_TERMS:
        for found in pattern.finditer(lowered):
            matches.append(ProhibitedMatch(category, term, found.span()))
    for category, term in _CJK_TERMS:
        start = 0
        while True:
            index = bounded.find(term, start)
            if index < 0:
                break
            matches.append(ProhibitedMatch(category, term, (index, index + len(term))))
            start = index + len(term)
    for term in extra_terms:
        if not isinstance(term, str) or not term.strip():
            continue
        needle = term.strip()
        haystack = bounded.lower() if needle.isascii() else bounded
        target = needle.lower() if needle.isascii() else needle
        start = 0
        while True:
            index = haystack.find(target, start)
            if index < 0:
                break
            matches.append(
                ProhibitedMatch(
                    ProhibitedCategory.HIGH_CONSEQUENCE, needle, (index, index + len(target))
                )
            )
            start = index + len(target)
    matches.sort(key=lambda item: (item.span[0], item.category.value, item.term))
    return tuple(matches)


def classify_labels(
    labels: Sequence[str],
    extra_terms: Sequence[str] = (),
) -> tuple[tuple[str, tuple[ProhibitedMatch, ...]], ...]:
    """Classify a bounded list of labels (for example an app list)."""

    return tuple(
        (label, scan_text_for_prohibited_terms(label, extra_terms)) for label in labels
    )


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    risk: RiskLevel
    categories: tuple[ProhibitedCategory, ...] = ()
    signals: tuple[Mapping[str, str], ...] = ()
    escalated: bool = False

    @property
    def prohibited(self) -> bool:
        return self.risk is RiskLevel.PROHIBITED


def assess_risk(
    declared: RiskLevel,
    *,
    matches: Sequence[ProhibitedMatch] = (),
    extra_signals: Sequence[RiskSignal] = (),
    transaction_known: bool = True,
    page_version_known: bool = True,
) -> RiskAssessment:
    """Combine the declared risk with page evidence; never lowers the risk."""

    categories: list[ProhibitedCategory] = []
    signals: list[Mapping[str, str]] = []
    if not transaction_known:
        categories.append(ProhibitedCategory.UNKNOWN_TRANSACTION)
    if not page_version_known:
        categories.append(ProhibitedCategory.UNKNOWN_PAGE_VERSION)
    highest = RiskLevel.PROHIBITED if categories else declared
    for match in matches:
        if match.category not in categories:
            categories.append(match.category)
        signals.append({"code": "PROHIBITED_TERM", "category": match.category.value})
        highest = RiskLevel.PROHIBITED
    for signal in extra_signals:
        signals.append({"code": signal.code, "risk": signal.risk.value})
        highest = escalate_risk(highest, signal.risk)
    if risk_rank(declared) > risk_rank(highest):  # pragma: no cover - defensive
        highest = declared
    return RiskAssessment(
        risk=highest,
        categories=tuple(categories),
        signals=tuple(signals),
        escalated=risk_rank(highest) > risk_rank(declared) or bool(categories),
    )


# Late import to avoid a cycle at module import time.
from personal_assistant.core.browser.models import RiskSignal  # noqa: E402

__all__ = [
    "MAX_SCAN_CHARS",
    "MAX_URL_LENGTH",
    "PROHIBITED_TERM_CATALOG",
    "NavigationDecision",
    "OriginPolicy",
    "ProhibitedCategory",
    "ProhibitedMatch",
    "RiskAssessment",
    "assess_risk",
    "classify_labels",
    "escalate_risk",
    "evaluate_navigation",
    "normalize_origin",
    "risk_rank",
    "scan_text_for_prohibited_terms",
]
