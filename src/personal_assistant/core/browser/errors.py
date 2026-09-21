"""Typed errors for the supervised browser boundary.

Every error carries a stable code and a short redacted message.  No error may
embed a URL query string, cookie, verification code, screenshot or raw HTML.
"""

from __future__ import annotations


class BrowserError(RuntimeError):
    code = "BROWSER_ERROR"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class BrowserPolicyError(BrowserError):
    """The request contradicts the supervised-browser policy."""

    code = "BROWSER_POLICY_DENIED"

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


class NavigationDeniedError(BrowserPolicyError):
    code = "BROWSER_NAVIGATION_DENIED"


class ProhibitedTransactionError(BrowserPolicyError):
    """R3: permanently prohibited, even with manifest flags or user clicks."""

    code = "BROWSER_PROHIBITED"


class BrowserUnavailableError(BrowserError):
    code = "BROWSER_UNAVAILABLE"


class BrowserSessionStateError(BrowserError):
    code = "BROWSER_SESSION_STATE"


class PageDriftError(BrowserError):
    code = "BROWSER_PAGE_DRIFT"


class PreviewExpiredError(BrowserError):
    code = "BROWSER_PREVIEW_EXPIRED"


class BrowserLimitError(BrowserError):
    code = "BROWSER_LIMIT_EXCEEDED"


class UnknownBrowserOutcomeError(BrowserError):
    """The action may have reached the site; never retried automatically."""

    code = "BROWSER_OUTCOME_UNKNOWN"


__all__ = [
    "BrowserError",
    "BrowserLimitError",
    "BrowserPolicyError",
    "BrowserSessionStateError",
    "BrowserUnavailableError",
    "NavigationDeniedError",
    "PageDriftError",
    "PreviewExpiredError",
    "ProhibitedTransactionError",
    "UnknownBrowserOutcomeError",
]
