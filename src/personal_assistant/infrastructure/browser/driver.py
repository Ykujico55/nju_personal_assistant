"""Playwright-backed headed browser driver used by the Desktop Companion.

This is the only module in the project that imports Playwright.  It runs inside
the companion process, in the logged-in user's desktop session, with
``headless=False``.  The driver:

* allow-lists every HTTP(S) request by exact origin and aborts the rest;
* blocks every non-GET request while a fill is in progress, so a page cannot
  save a draft during the supervised fill step;
* reads a bounded, accessibility/DOM structure instead of raw HTML;
* never calls ``page.evaluate``, never touches password fields, never captures
  screenshots and never exports cookies or storage state.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import hashlib
import io
import ipaddress
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from personal_assistant.core.browser import evaluate_navigation
from personal_assistant.core.browser.models import MAX_FIELD_VALUE_CHARS
from personal_assistant.core.browser.policy import scan_text_for_prohibited_terms

ACTION_SELECTOR = "button, input[type=submit], input[type=button]"
GET_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_CONTROLS = 512
MAX_ACTIONS = 32
MAX_FORMS = 32
MAX_ACTION_LABEL = 200
MAX_PAGE_TEXT_CHARS = 65536
MAX_RECEIPT_CHARS = 500
MAX_LINKS = 100
COMMAND_DEADLINE_SECONDS = 30.0
CLOSE_GRACE_SECONDS = 5.0


class BrowserDriverError(RuntimeError):
    """Typed companion-side browser failure; never carries page content."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _origin_of(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    if parts.port in (None, 443):
        return f"https://{host}"
    return f"https://{host}:{parts.port}"


def _navigation_target_matches(
    allowance: tuple[str, str, str, int], method: str, url: str
) -> bool:
    expected_method, expected_origin, expected_path, remaining = allowance
    if remaining <= 0 or method != expected_method:
        return False
    if _origin_of(url) != expected_origin:
        return False
    try:
        path = urlsplit(url).path or "/"
    except ValueError:
        return False
    return path == expected_path


def _is_main_frame_document(request: Any, page: Any) -> bool:
    """True only for a top-frame document navigation (a real form submit)."""

    try:
        if not request.is_navigation_request():
            return False
        with contextlib.suppress(Exception):
            if str(request.resource_type) != "document":
                return False
        return request.frame is page.main_frame
    except Exception:
        return False


def _browser_pid_from_process_info(info: Any) -> int | None:
    if not isinstance(info, Mapping):
        return None
    for item in info.get("processInfo", []):
        if isinstance(item, Mapping) and item.get("type") == "browser":
            raw_id = item.get("id")
            if isinstance(raw_id, int):
                return int(raw_id)
    return None


def _allowance_matches(
    allowance: tuple[str, str, str, int], method: str, url: str
) -> bool:
    expected_method, expected_origin, expected_path, remaining = allowance
    if remaining <= 0 or method != expected_method:
        return False
    if _origin_of(url) != expected_origin:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    # The bound target has no query string by contract: a request that adds or
    # rewrites query parameters is a different target and never matches.
    if parts.query:
        return False
    return (parts.path or "/") == expected_path


def _is_loopback_origin(origin: str) -> bool:
    parts = urlsplit(origin)
    host = (parts.hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _bounded(text: Any, limit: int) -> str:
    value = text if isinstance(text, str) else ("" if text is None else str(text))
    return " ".join(value.split())[:limit]


@dataclass(slots=True)
class ControlMeta:
    locator: str
    index: int
    tag: str
    type: str
    name: str
    element_id: str
    required: bool
    readonly: bool
    disabled: bool
    options: tuple[str, ...]
    max_length: int


@dataclass(slots=True)
class PageRead:
    url: str
    title: str
    login_page: bool
    controls: list[ControlMeta] = field(default_factory=list)
    actions: list[Mapping[str, Any]] = field(default_factory=list)
    signals: list[Mapping[str, str]] = field(default_factory=list)
    text_digest: str = ""
    byte_size: int = 0
    truncated: bool = False


class PlaywrightHeadedDriver:
    def __init__(
        self,
        *,
        allowed_origins: Sequence[str],
        test_mode: bool = False,
        cdp_port: int | None = None,
        allow_insecure_loopback_tls: bool = False,
        command_deadline_seconds: float = COMMAND_DEADLINE_SECONDS,
        baseline_chrome_pids: Sequence[int] = (),
    ) -> None:
        self._allowed_origins = frozenset(allowed_origins)
        self._test_mode = test_mode
        self._cdp_port = cdp_port
        self._baseline_chrome_pids = frozenset(int(item) for item in baseline_chrome_pids)
        self._allow_insecure = bool(allow_insecure_loopback_tls) and all(
            _is_loopback_origin(origin) for origin in self._allowed_origins
        )
        if allow_insecure_loopback_tls and not self._allow_insecure:
            raise BrowserDriverError(
                "INSECURE_TLS_ONLY_FOR_LOOPBACK",
                "insecure TLS is only allowed for loopback test origins",
            )
        self._deadline = command_deadline_seconds
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        # Every non-GET request is blocked by default.  A one-shot, fully
        # bound allowance is granted only inside the R2 submit critical
        # section; a login challenge is the only other exception and the
        # window closes as soon as the driver sees a non-login page.
        self._mutations_blocked = False
        self._login_window = False
        self._login_paths: tuple[str, ...] = ()
        # The frozen authentication target (method, origin, path) and its
        # one-shot allowance: only the real login form submit may use it.
        self._login_allowance: tuple[str, str, str, int] | None = None
        self._login_armed_target: tuple[str, str, str] | None = None
        self._submit_allowance: tuple[str, str, str, int] | None = None
        self.allowed_write_requests = 0
        self.user_auth_requests = 0
        self._control_meta: dict[str, ControlMeta] = {}
        self._control_values: dict[str, str] = {}
        self._action_locators: dict[str, int] = {}
        self._last_read: PageRead | None = None
        self._chrome_pids: tuple[int, ...] = ()
        self.headless = False
        self.blocked_origin_requests = 0
        self.blocked_mutating_requests = 0
        self.autosave_attempts = 0
        self.navigations = 0
        self.fill_operations = 0
        self.click_operations = 0
        self.browser_process_id: int | None = None

    # ------------------------------------------------------------------ setup

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover - depends on optional extra
            raise BrowserDriverError(
                "BROWSER_UNAVAILABLE", "the Playwright runtime is not installed"
            ) from exc
        args: list[str] = []
        if self._test_mode and self._cdp_port:
            args.append(f"--remote-debugging-port={self._cdp_port}")
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=False,
                args=args,
                timeout=self._deadline * 1000,
            )
        except Exception as exc:
            await self._force_stop()
            raise BrowserDriverError(
                "BROWSER_LAUNCH_FAILED", "the headed browser could not be launched"
            ) from exc
        self.headless = False
        try:
            self._context = await self._browser.new_context(
                accept_downloads=False,
                ignore_https_errors=self._allow_insecure,
                service_workers="block",
            )
            await self._context.route("**/*", self._route)
            self._page = await self._context.new_page()
        except BaseException:
            # A partially started browser must never outlive the failed session.
            await self._force_stop()
            raise
        if self._test_mode:
            fresh = await self._fresh_chrome_pids()
            self._chrome_pids = fresh
            with contextlib.suppress(Exception):
                self.browser_process_id = await self._browser_process_id()
            if self.browser_process_id is None and fresh:
                self.browser_process_id = fresh[0]

    async def _fresh_chrome_pids(self) -> tuple[int, ...]:
        """Poll briefly so a just-spawned Chromium is observable."""

        deadline = asyncio.get_running_loop().time() + 3.0
        while True:
            fresh = tuple(
                pid for pid in _snapshot_chrome_pids() if pid not in self._baseline_chrome_pids
            )
            if fresh or asyncio.get_running_loop().time() >= deadline:
                return fresh
            await asyncio.sleep(0.2)

    async def _browser_process_id(self) -> int | None:
        """Resolve the real Chromium browser PID.

        The browser-level CDP session is authoritative; a page-level session is
        only a fallback, and the result is cached once observed.
        """

        if self.browser_process_id is not None:
            return self.browser_process_id
        if self._browser is None:
            return await self._browser_pid_from_page_session()
        for attempt in range(4):
            if attempt:
                await asyncio.sleep(0.25)
            session = None
            try:
                session = await self._browser.new_browser_cdp_session()
                info = await session.send("SystemInfo.getProcessInfo")
            except Exception:
                info = None
            finally:
                if session is not None:
                    with contextlib.suppress(Exception):
                        await session.detach()
            pid = _browser_pid_from_process_info(info)
            if pid is not None:
                return pid
        return await self._browser_pid_from_page_session()

    async def _browser_pid_from_page_session(self) -> int | None:
        if self._context is None or self._page is None:
            return None
        session = None
        try:
            session = await self._context.new_cdp_session(self._page)
            info = await session.send("SystemInfo.getProcessInfo")
        except Exception:
            return None
        finally:
            if session is not None:
                with contextlib.suppress(Exception):
                    await session.detach()
        return _browser_pid_from_process_info(info)

    async def _route(self, route: Any) -> None:
        request = route.request
        url = str(request.url)
        method = str(request.method).upper()
        decision = evaluate_navigation(url, allowed_origins=self._allowed_origins)
        if not decision.allowed:
            self.blocked_origin_requests += 1
            await route.abort()
            return
        if method not in GET_METHODS:
            allowance = self._submit_allowance
            if (
                allowance is not None
                and _allowance_matches(allowance, method, url)
                and _is_main_frame_document(route.request, self._page)
            ):
                remaining = allowance[3] - 1
                self._submit_allowance = (
                    (allowance[0], allowance[1], allowance[2], remaining)
                    if remaining > 0
                    else None
                )
                self.allowed_write_requests += 1
                await route.continue_()
                return
            login_allowance = self._login_allowance
            if (
                login_allowance is not None
                and _navigation_target_matches(login_allowance, method, url)
                and _is_main_frame_document(route.request, self._page)
            ):
                # One user-driven main-frame submission of the frozen login
                # form; background fetch/XHR requests never qualify.
                remaining = login_allowance[3] - 1
                self._login_allowance = (
                    (login_allowance[0], login_allowance[1], login_allowance[2], remaining)
                    if remaining > 0
                    else None
                )
                # Clear the frozen target so the next observation can re-arm a
                # retry on the same challenge page after a failed login POST.
                self._login_armed_target = None
                self.user_auth_requests += 1
                await route.continue_()
                return
            # Default: abort.  Page-initiated writes (autosave, SPA drafts,
            # rewrite attempts) are never allowed, before or after a fill.
            self.blocked_mutating_requests += 1
            self.autosave_attempts += 1
            await route.abort()
            return
        await route.continue_()

    # --------------------------------------------------------------- commands

    async def navigate(self, url: str, *, login_paths: Sequence[str] = ()) -> Mapping[str, Any]:
        page = self._require_page()
        self.navigations += 1
        try:
            async with asyncio.timeout(self._deadline):
                await page.goto(url, wait_until="domcontentloaded")
        except TimeoutError as exc:
            raise BrowserDriverError("BROWSER_TIMEOUT", "navigation timed out") from exc
        except BrowserDriverError:
            raise
        except Exception as exc:
            raise BrowserDriverError("BROWSER_NAVIGATION_FAILED", "navigation failed") from exc
        current = str(page.url)
        self._login_paths = tuple(login_paths)
        login_page = await self._observe_login(page, login_paths)
        return {
            "url": current,
            "origin": _origin_of(current),
            "path": urlsplit(current).path or "/",
            "login_page": login_page,
        }

    async def snapshot(
        self,
        *,
        prohibited_terms: Sequence[str] = (),
        scan_text: bool = False,
        login_paths: Sequence[str] = (),
    ) -> Mapping[str, Any]:
        page = self._require_page()
        text_result = await self._page_text(page) if scan_text else None
        if login_paths:
            self._login_paths = tuple(login_paths)
        read = await self._read_page(page, login_paths=self._login_paths or login_paths)
        await self._observe_login(
            page, self._login_paths or login_paths, login_page=read.login_page
        )
        self._last_read = read
        signals: list[Mapping[str, str]] = list(read.signals)
        scan_incomplete = False
        if scan_text:
            text, complete = text_result if text_result is not None else ("", False)
            scan_incomplete = not complete
            for match in scan_text_for_prohibited_terms(text, prohibited_terms):
                signals.append(
                    {
                        "code": "PROHIBITED_TERM",
                        "detail": match.category.value,
                        "risk": "PROHIBITED",
                    }
                )
        links = await self._read_links(page)
        forms = await self._read_forms(page)
        structure = {
            "controls": [
                {
                    "locator": item.locator,
                    "tag": item.tag,
                    "type": item.type,
                    "name": item.name,
                    "element_id": item.element_id,
                    "value": self._control_values.get(item.locator, ""),
                    "required": item.required,
                    "readonly": item.readonly,
                    "disabled": item.disabled,
                    "options": list(item.options),
                    "max_length": item.max_length,
                }
                for item in read.controls
            ],
            "headings": [
                {"level": 1, "text": read.title},
            ],
            "links": links,
            "forms": forms,
        }
        return {
            "url": read.url,
            "title": read.title,
            "login_page": read.login_page,
            "structure": structure,
            "actions": list(read.actions),
            "links": links,
            "signals": signals,
            "text_digest": _sha256_text(text) if scan_text else "",
            "byte_size": len(text.encode("utf-8", "replace")) if scan_text else 0,
            "truncated": scan_incomplete,
            "scan_incomplete": scan_incomplete,
        }

    async def fill(
        self, fields: Sequence[tuple[str, str]]
    ) -> Mapping[str, Any]:
        page = self._require_page()
        self._mutations_blocked = True
        self._login_window = False
        self._login_allowance = None
        self._login_armed_target = None
        applied = 0
        for locator, value in fields:
            meta = self._control_meta.get(locator)
            if meta is None:
                raise BrowserDriverError("BROWSER_UNKNOWN_FIELD", "unknown field locator")
            if meta.type == "password":
                raise BrowserDriverError(
                    "BROWSER_PASSWORD_FIELD", "password fields are never filled"
                )
            if meta.disabled:
                raise BrowserDriverError("BROWSER_FIELD_DISABLED", "the field is disabled")
            if meta.type == "file":
                raise BrowserDriverError(
                    "BROWSER_UPLOAD_FORBIDDEN", "file upload is not allowed"
                )
            element = self._control_element(page, meta)
            try:
                async with asyncio.timeout(self._deadline):
                    await self._apply_value(element, meta, value)
            except TimeoutError as exc:
                raise BrowserDriverError("BROWSER_TIMEOUT", "field fill timed out") from exc
            applied += 1
        # Stay in the FILL phase briefly so queued autosave requests are still
        # intercepted and aborted.  The next command resets the phase.
        await asyncio.sleep(0.25)
        self.fill_operations += 1
        return {"applied": applied}

    async def _apply_value(self, element: Any, meta: ControlMeta, value: str) -> None:
        if meta.tag == "select":
            await element.select_option(value)
            return
        if meta.type in {"checkbox", "radio"}:
            if value.lower() in {"true", "1", "yes", "on"}:
                await element.check()
            else:
                await element.uncheck()
            return
        await element.fill(value)

    async def find_text(self, query: str) -> Mapping[str, Any]:
        page = self._require_page()
        try:
            locator = page.get_by_text(query, exact=False)
            async with asyncio.timeout(self._deadline):
                count = await locator.count()
                if count == 0:
                    return {"found": False, "count": 0, "excerpt": ""}
                excerpt = _bounded(await locator.first.inner_text(), MAX_RECEIPT_CHARS)
        except TimeoutError as exc:
            raise BrowserDriverError("BROWSER_TIMEOUT", "text search timed out") from exc
        except Exception as exc:
            raise BrowserDriverError("BROWSER_SEARCH_FAILED", "text search failed") from exc
        return {"found": True, "count": min(int(count), 100), "excerpt": excerpt}

    async def activate_navigation(
        self, *, locator: str, expected_path: str
    ) -> Mapping[str, Any]:
        """Click one declared navigation action and verify the landing path.

        Navigation is a read-side route: no receipt is expected, no write
        allowance is granted and the landing page fingerprint is verified by
        the caller afterwards.
        """

        page = self._require_page()
        index = self._action_locators.get(locator)
        if index is None:
            raise BrowserDriverError("BROWSER_UNKNOWN_ACTION", "unknown action locator")
        expected = expected_path.strip()
        if not expected.startswith("/") or ".." in expected or "?" in expected:
            raise BrowserDriverError(
                "BROWSER_NAVIGATION_TARGET_INVALID", "the navigation target is not static"
            )
        element = page.locator(ACTION_SELECTOR).nth(index)
        blocked_before = self.blocked_mutating_requests
        try:
            async with asyncio.timeout(self._deadline):
                await element.click()
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                with contextlib.suppress(Exception):
                    await page.wait_for_timeout(500)
        except TimeoutError as exc:
            raise BrowserDriverError("BROWSER_TIMEOUT", "navigation click timed out") from exc
        except BrowserDriverError:
            raise
        except Exception as exc:
            raise BrowserDriverError(
                "BROWSER_CLICK_FAILED", "the navigation click failed"
            ) from exc
        url = str(page.url)
        parts = urlsplit(url)
        landed = f"{parts.path or '/'}#{parts.fragment}" if parts.fragment else (
            parts.path or "/"
        )
        if landed != expected:
            raise BrowserDriverError(
                "BROWSER_NAVIGATION_MISMATCH",
                "the click did not land on the expected path",
            )
        return {
            "clicked": True,
            "url": url,
            "path": landed,
            "blocked_mutating_requests": (
                self.blocked_mutating_requests - blocked_before
            ),
        }

    async def click(
        self,
        *,
        action_id: str,
        locator: str,
        receipt_locator: str,
        expected_method: str,
        expected_origin: str,
        expected_path: str,
    ) -> Mapping[str, Any]:
        page = self._require_page()
        index = self._action_locators.get(locator)
        if index is None:
            raise BrowserDriverError("BROWSER_UNKNOWN_ACTION", "unknown action locator")
        method = expected_method.strip().upper()
        if method in GET_METHODS or not expected_path.startswith("/"):
            raise BrowserDriverError(
                "BROWSER_SUBMIT_TARGET_INVALID", "the submit target is not bound"
            )
        self._submit_allowance = (method, expected_origin, expected_path, 1)
        self.click_operations += 1
        writes_before = self.allowed_write_requests
        try:
            pre_receipt = await self._receipt(page, receipt_locator)
            element = page.locator(ACTION_SELECTOR).nth(index)
            try:
                async with asyncio.timeout(self._deadline):
                    await element.click()
                    with contextlib.suppress(Exception):
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except TimeoutError as exc:
                raise BrowserDriverError("BROWSER_TIMEOUT", "click timed out") from exc
            except BrowserDriverError:
                raise
            except Exception as exc:
                raise BrowserDriverError("BROWSER_CLICK_FAILED", "the click failed") from exc
            url = str(page.url)
            post_receipt = await self._receipt(page, receipt_locator)
            # A receipt counts only when the page changed after the click AND
            # the bound write request was actually released: DOM text alone
            # (a script rewriting the page) must never be reported as success.
            fresh = post_receipt is not None and post_receipt != pre_receipt
            writes_allowed = self.allowed_write_requests - writes_before
            confirmed = fresh and writes_allowed == 1
            return {
                "clicked": True,
                "outcome": "RECEIPT" if confirmed else "UNKNOWN",
                "url": url,
                "receipt_found": confirmed,
                "receipt_text": post_receipt or "",
                "action_id": action_id,
                "write_requests_allowed": writes_allowed,
                "allowed_write_requests": self.allowed_write_requests,
                "blocked_during_submit": self.blocked_mutating_requests,
            }
        finally:
            # The allowance lasts only for the click critical section; the
            # session stays mutation-blocked afterwards.
            self._submit_allowance = None

    async def collect_matches(
        self, *, pattern: str, limit: int, url: str = ""
    ) -> Mapping[str, Any]:
        """Return bounded regex matches from page text (never the text).

        With ``url`` the driver opens a short-lived, read-only tab for that URL
        (requests still pass the context-wide origin interception) so a
        baseline can be collected without disturbing the supervised page.
        """

        if not pattern or len(pattern) > 256:
            raise BrowserDriverError(
                "BROWSER_PATTERN_INVALID", "the collection pattern is required"
            )
        if limit < 1 or limit > 256:
            raise BrowserDriverError(
                "BROWSER_LIMIT_EXCEEDED", "the collection limit is out of range"
            )
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise BrowserDriverError(
                "BROWSER_PATTERN_INVALID", "the collection pattern is not a regex"
            ) from exc
        page = self._require_page()
        tab = None
        if url:
            try:
                async with asyncio.timeout(self._deadline):
                    tab = await self._context.new_page()
                    await tab.goto(url, wait_until="domcontentloaded")
            except Exception as exc:
                if tab is not None:
                    with contextlib.suppress(Exception):
                        await tab.close()
                raise BrowserDriverError(
                    "BROWSER_NAVIGATION_FAILED", "the collection page did not open"
                ) from exc
            page = tab
        try:
            text, complete = await self._page_text(page)
        finally:
            if tab is not None:
                with contextlib.suppress(Exception):
                    await tab.close()
        matches: list[str] = []
        for match in compiled.findall(text):
            value = match if isinstance(match, str) else next(
                (item for item in match if isinstance(item, str)), ""
            )
            if not value:
                continue
            matches.append(value[:128])
            if len(matches) >= limit:
                break
        return {
            "matches": matches,
            "truncated": (not complete) or len(matches) >= limit,
        }

    async def _receipt(self, page: Any, receipt_locator: str) -> str | None:
        if not receipt_locator:
            return None
        kind, _, reference = receipt_locator.partition(":")
        try:
            if kind == "text":
                locator = page.get_by_text(reference, exact=False).first
            elif kind == "ctl":
                _, group, index = receipt_locator.split(":")
                selector = ("input:not([type='hidden'])", "select", "textarea")[int(group)]
                locator = page.locator(selector).nth(int(index))
            elif kind == "act":
                locator = page.locator(ACTION_SELECTOR).nth(int(reference))
            else:
                return None
            async with asyncio.timeout(self._deadline):
                if await locator.count() == 0:
                    return None
                text = _bounded(await locator.inner_text(), MAX_RECEIPT_CHARS)
                return text
        except Exception:
            return None

    async def close(self) -> None:
        for target in (self._page, self._context, self._browser):
            if target is None:
                continue
            try:
                async with asyncio.timeout(CLOSE_GRACE_SECONDS):
                    await target.close()
            except Exception:
                break
        await self._force_stop()

    async def _force_stop(self) -> None:
        playwright = self._playwright
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        if playwright is not None:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(CLOSE_GRACE_SECONDS):
                    await playwright.stop()

    async def diagnostics(self) -> Mapping[str, Any]:
        connected = False
        with contextlib.suppress(Exception):
            connected = bool(self._browser is not None and self._browser.is_connected())
        url = ""
        login_page = False
        if self._page is not None:
            with contextlib.suppress(Exception):
                url = str(self._page.url)
            login_page = await self._observe_login(self._page, self._login_paths)
        return {
            "url": url,
            "login_page": login_page,
            "login_window": self._login_window,
            "login_target": list(self._login_armed_target or ()),
            "user_auth_requests": self.user_auth_requests,
            "headless": self.headless,
            "browser_connected": connected,
            "browser_process_id": self.browser_process_id,
            "chrome_pids": list(self._chrome_pids) if self._test_mode else [],
            "navigations": self.navigations,
            "blocked_origin_requests": self.blocked_origin_requests,
            "blocked_mutating_requests": self.blocked_mutating_requests,
            "autosave_attempts": self.autosave_attempts,
            "fill_operations": self.fill_operations,
            "click_operations": self.click_operations,
            "allowed_write_requests": self.allowed_write_requests,
            "mutations_blocked": self._mutations_blocked,
            "writes_blocked_by_default": not self._login_window,
            "test_mode": self._test_mode,
        }

    # --------------------------------------------------------------- internals

    def _require_page(self) -> Any:
        if self._page is None:
            raise BrowserDriverError("BROWSER_UNAVAILABLE", "the browser is not running")
        return self._page

    async def _observe_login(
        self,
        page: Any,
        login_paths: Sequence[str],
        *,
        login_page: bool | None = None,
    ) -> bool:
        """Track the challenge window and freeze the form's write target."""

        detected = (
            await self._detect_login(page, login_paths)
            if login_page is None
            else login_page
        )
        if detected and login_paths and not self._matches_login_path(page, login_paths):
            # A page that merely contains a password input outside the declared
            # authentication paths must not arm the one-shot login write window
            # nor be reported as the challenge.
            self._login_allowance = None
            self._login_armed_target = None
            self._login_window = False
            return False
        if detected and not self._mutations_blocked:
            target = await self._login_form_target(page)
            if target is not None and target != self._login_armed_target:
                self._login_allowance = (target[0], target[1], target[2], 1)
                self._login_armed_target = target
            self._login_window = True
        else:
            self._login_allowance = None
            self._login_armed_target = None
            self._login_window = False
        return detected

    @staticmethod
    def _matches_login_path(page: Any, login_paths: Sequence[str]) -> bool:
        path = urlsplit(str(page.url)).path or "/"
        for pattern in login_paths:
            if path == pattern or path.startswith(pattern.rstrip("*")):
                return True
        return False

    async def _login_form_target(self, page: Any) -> tuple[str, str, str] | None:
        """Freeze the challenge form's exact write target (method/origin/path)."""

        with contextlib.suppress(Exception):
            forms = page.locator("form")
            count = await forms.count()
            for index in range(min(int(count), 8)):
                form = forms.nth(index)
                if await form.locator("input[type=password]").count() == 0:
                    continue
                method = str(await form.get_attribute("method") or "get").upper()
                if method in GET_METHODS:
                    return None
                action = str(await form.get_attribute("action") or "")
                base = str(page.url)
                target = urljoin(base, action) if action else base
                parts = urlsplit(target)
                if not parts.hostname or _origin_of(target) not in self._allowed_origins:
                    return None
                return method, _origin_of(target), parts.path or "/"
        return None

    async def _detect_login(self, page: Any, login_paths: Sequence[str]) -> bool:
        path = urlsplit(str(page.url)).path or "/"
        for pattern in login_paths:
            if path == pattern or path.startswith(pattern.rstrip("*")):
                return True
        with contextlib.suppress(Exception):
            if await page.locator("input[type=password]").count() > 0:
                return True
        return False

    async def _read_page(self, page: Any, *, login_paths: Sequence[str]) -> PageRead:
        controls = await self._read_controls(page)
        actions = await self._read_actions(page)
        title = _bounded(await page.title(), 200)
        url = str(page.url)
        return PageRead(
            url=url,
            title=title,
            login_page=await self._detect_login(page, login_paths),
            controls=controls,
            actions=actions,
            signals=[],
            text_digest="",
            byte_size=0,
            truncated=False,
        )

    async def _read_controls(self, page: Any) -> list[ControlMeta]:
        controls: list[ControlMeta] = []
        groups: tuple[tuple[str, str], ...] = (
            ("input", "input:not([type='hidden'])"),
            ("select", "select"),
            ("textarea", "textarea"),
        )
        for group_index, (tag, selector) in enumerate(groups):
            element = page.locator(selector)
            count = await element.count()
            if len(controls) + count > MAX_CONTROLS:
                raise BrowserDriverError("BROWSER_LIMIT_EXCEEDED", "too many page controls")
            for index in range(count):
                handle = element.nth(index)
                type_ = str(await handle.get_attribute("type") or "").lower()
                name = str(await handle.get_attribute("name") or "")
                element_id = str(await handle.get_attribute("id") or "")
                required = (await handle.get_attribute("required")) is not None
                readonly = (await handle.get_attribute("readonly")) is not None
                disabled = (await handle.get_attribute("disabled")) is not None
                options: tuple[str, ...] = ()
                if tag == "select":
                    option_elements = handle.locator("option")
                    option_count = await option_elements.count()
                    values: list[str] = []
                    for option_index in range(min(int(option_count), 64)):
                        option = option_elements.nth(option_index)
                        raw_value = await option.get_attribute("value")
                        if raw_value is None:
                            raw_value = await option.inner_text()
                        values.append(_bounded(raw_value, 100))
                    options = tuple(values)
                max_length = 0
                raw_max: Any = await handle.get_attribute("maxlength")
                if isinstance(raw_max, str) and raw_max.isdigit():
                    max_length = int(raw_max)
                current = await self._read_value(handle, tag, type_)
                locator = f"ctl:{group_index}:{index}"
                controls.append(
                    ControlMeta(
                        locator=locator,
                        index=index,
                        tag=tag,
                        type=type_ or ("select" if tag == "select" else tag),
                        name=name,
                        element_id=element_id,
                        required=required,
                        readonly=readonly,
                        disabled=disabled,
                        options=options,
                        max_length=max_length,
                    )
                )
                self._control_values[locator] = current
        self._control_meta = {item.locator: item for item in controls}
        return controls

    async def _read_value(self, handle: Any, tag: str, type_: str) -> str:
        if type_ == "password":
            # Password material never leaves the browser process.
            return ""
        try:
            if tag == "select":
                return str(await handle.input_value())[:MAX_FIELD_VALUE_CHARS]
            if type_ in {"checkbox", "radio"}:
                return "true" if await handle.is_checked() else "false"
            if type_ == "file":
                return ""
            return str(await handle.input_value())[:MAX_FIELD_VALUE_CHARS]
        except Exception:
            return ""

    def _control_element(self, page: Any, meta: ControlMeta) -> Any:
        selector = {
            "input": "input:not([type='hidden'])",
            "select": "select",
            "textarea": "textarea",
        }[meta.tag]
        return page.locator(selector).nth(meta.index)

    async def _read_actions(self, page: Any) -> list[Mapping[str, Any]]:
        element = page.locator(ACTION_SELECTOR)
        count = await element.count()
        if count > MAX_ACTIONS:
            raise BrowserDriverError("BROWSER_LIMIT_EXCEEDED", "too many page actions")
        actions: list[Mapping[str, Any]] = []
        self._action_locators = {}
        for index in range(count):
            handle = element.nth(index)
            label = _bounded(await handle.inner_text(), MAX_ACTION_LABEL)
            if not label:
                label = _bounded(await handle.get_attribute("value") or "", MAX_ACTION_LABEL)
            item_type = str(await handle.get_attribute("type") or "").lower()
            kind = "submit" if item_type == "submit" else "other"
            locator = f"act:{index}"
            self._action_locators[locator] = index
            actions.append({"locator": locator, "label": label, "kind": kind})
        return actions

    async def _read_links(self, page: Any) -> list[Mapping[str, str]]:
        element = page.locator("a[href]")
        count = await element.count()
        base = str(page.url)
        links: list[Mapping[str, str]] = []
        for index in range(min(int(count), MAX_LINKS)):
            handle = element.nth(index)
            href = await handle.get_attribute("href")
            if not href:
                continue
            absolute = urljoin(base, href)
            decision = evaluate_navigation(absolute, allowed_origins=self._allowed_origins)
            if not decision.allowed:
                continue
            links.append(
                {"text": _bounded(await handle.inner_text(), 200), "path": decision.path}
            )
        return links

    async def _page_text(self, page: Any) -> tuple[str, bool]:
        """Return (text, complete).  A failed or truncated read is never "no risk"."""

        try:
            async with asyncio.timeout(self._deadline):
                text = await page.locator("body").inner_text(
                    timeout=self._deadline * 1000
                )
        except Exception:
            return "", False
        value = str(text)
        if len(value) > MAX_PAGE_TEXT_CHARS:
            return value[:MAX_PAGE_TEXT_CHARS], False
        return value, True

    async def _read_forms(self, page: Any) -> list[Mapping[str, str]]:
        element = page.locator("form")
        count = await element.count()
        forms: list[Mapping[str, str]] = []
        for index in range(min(int(count), MAX_FORMS)):
            handle = element.nth(index)
            forms.append(
                {
                    "action": _bounded(await handle.get_attribute("action") or "", 512),
                    "method": _bounded(
                        await handle.get_attribute("method") or "get", 16
                    ).lower(),
                    "element_id": _bounded(await handle.get_attribute("id") or "", 256),
                    "name": _bounded(await handle.get_attribute("name") or "", 256),
                }
            )
        return forms


def _snapshot_chrome_pids() -> tuple[int, ...]:
    """Best-effort process snapshot used only for test-mode diagnostics."""

    if sys.platform != "win32":
        return ()
    try:
        output = subprocess.run(
            ["tasklist", "/FO", "CSV", "/FI", "IMAGENAME eq chrome.exe"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return ()
    pids: list[int] = []
    reader = csv.reader(io.StringIO(output))
    next(reader, None)
    for row in reader:
        if len(row) >= 2 and row[0].lower().startswith("chrome"):
            with contextlib.suppress(ValueError):
                pids.append(int(row[1]))
    return tuple(pids)


__all__ = [
    "ACTION_SELECTOR",
    "CLOSE_GRACE_SECONDS",
    "COMMAND_DEADLINE_SECONDS",
    "BrowserDriverError",
    "PlaywrightHeadedDriver",
]
