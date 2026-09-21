"""Desktop Companion session and capability management.

The companion is the only process that may drive a visible browser.  It runs in
the logged-in user's desktop session, binds a loopback port, and authenticates
every call with a 256-bit capability that is:

* generated with ``secrets.token_urlsafe(32)`` (256 bits of entropy);
* bound to exactly one session and its purpose;
* valid for a bounded TTL and revocable;
* kept in memory only and gone after a process restart.

It never writes cookies, storage state, passwords, verification codes or
screenshots to disk and never logs request bodies.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from personal_assistant.core.browser import evaluate_navigation

CAPABILITY_TTL_SECONDS = 600.0
MAX_COMPANION_SESSIONS = 4


class CompanionError(RuntimeError):
    def __init__(self, code: str, message: str = "", *, status: int = 400) -> None:
        super().__init__(message or code)
        self.code = code
        self.status = status


@dataclass(slots=True)
class CompanionSession:
    session_id: str
    purpose: str
    task_id: str
    extension_id: str
    allowed_origins: tuple[str, ...]
    capability: str
    created_at: float
    expires_at: float
    driver: Any
    revoked: bool = False
    fill_operations: int = 0

    def expired(self, now: float) -> bool:
        return now >= self.expires_at

    def public(self) -> Mapping[str, Any]:
        return {
            "session_id": self.session_id,
            "purpose": self.purpose,
            "task_id": self.task_id,
            "extension_id": self.extension_id,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
        }


class DesktopCompanion:
    def __init__(
        self,
        *,
        driver_factory: Callable[..., Any],
        ttl_seconds: float = CAPABILITY_TTL_SECONDS,
        max_sessions: int = MAX_COMPANION_SESSIONS,
        test_mode: bool = False,
        now: Callable[[], float] | None = None,
        driver_options: Mapping[str, Any] | None = None,
    ) -> None:
        self._driver_factory = driver_factory
        if not 1 <= ttl_seconds <= 3600:
            raise CompanionError("CAPABILITY_TTL_INVALID", "capability TTL is out of range")
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._test_mode = test_mode
        self._now = now or time.monotonic
        self._driver_options = dict(driver_options or {})
        self._root_capability = secrets.token_urlsafe(32)
        self._sessions: dict[str, CompanionSession] = {}
        self._lock = asyncio.Lock()

    @property
    def root_capability(self) -> str:
        return self._root_capability

    @property
    def test_mode(self) -> bool:
        return self._test_mode

    async def authorize(self, session_id: str, *, token: str) -> None:
        """Validate a session capability without touching the driver."""

        await self._require_session(session_id, token)

    def authorize_root(self, *, token: str) -> None:
        """Validate the one-shot root capability without parsing a body."""

        self._require_root(token)

    async def create_session(
        self,
        *,
        session_id: str,
        purpose: str,
        allowed_origins: Sequence[str],
        task_id: str,
        extension_id: str,
        token: str,
    ) -> Mapping[str, Any]:
        self._require_root(token)
        origins = tuple(dict.fromkeys(str(item) for item in allowed_origins))
        if not origins:
            raise CompanionError("ORIGINS_REQUIRED", "at least one origin is required")
        now = self._now()
        await self._sweep(now)
        async with self._lock:
            if len(self._sessions) >= self._max_sessions:
                raise CompanionError(
                    "SESSION_LIMIT", "too many supervised sessions", status=429
                )
            if session_id in self._sessions:
                raise CompanionError("SESSION_EXISTS", "session id already exists", status=409)
            driver = self._driver_factory(allowed_origins=origins, **self._driver_options)
            session = CompanionSession(
                session_id=session_id,
                purpose=purpose[:500],
                task_id=task_id[:200],
                extension_id=extension_id[:200],
                allowed_origins=origins,
                capability=secrets.token_urlsafe(32),
                created_at=now,
                expires_at=now + self._ttl,
                driver=driver,
            )
            self._sessions[session_id] = session
        try:
            await driver.start()
        except BaseException:
            async with self._lock:
                self._sessions.pop(session_id, None)
            raise
        return {
            "session_id": session_id,
            "capability": session.capability,
            "url": "",
            "origin": "",
            "expires_at": session.expires_at,
        }

    async def status(self, session_id: str, *, token: str) -> Mapping[str, Any]:
        session = await self._require_session(session_id, token)
        payload: dict[str, Any] = {
            "session_id": session_id,
            "open": True,
            "url": "",
            "origin": "",
            "path": "/",
            "login_page": False,
            "headless": False,
            "browser_alive": True,
            "re_navigations": 0,
            "blocked_origin_requests": 0,
            "blocked_mutating_requests": 0,
            "fill_operations": session.fill_operations,
            "click_operations": 0,
        }
        with contextlib.suppress(Exception):
            payload.update(await session.driver.diagnostics())
        return payload

    async def navigate(
        self, session_id: str, *, url: str, login_paths: Sequence[str], token: str
    ) -> Mapping[str, Any]:
        session = await self._require_session(session_id, token)
        decision = evaluate_navigation(url, allowed_origins=set(session.allowed_origins))
        if not decision.allowed:
            raise CompanionError("NAVIGATION_DENIED", decision.reason, status=403)
        try:
            return dict(await session.driver.navigate(url, login_paths=tuple(login_paths)))
        except Exception as exc:
            raise self._driver_error(exc) from exc

    async def snapshot(
        self,
        session_id: str,
        *,
        prohibited_terms: Sequence[str],
        scan_text: bool,
        login_paths: Sequence[str],
        token: str,
    ) -> Mapping[str, Any]:
        session = await self._require_session(session_id, token)
        try:
            return dict(
                await session.driver.snapshot(
                    prohibited_terms=tuple(prohibited_terms),
                    scan_text=scan_text,
                    login_paths=tuple(login_paths),
                )
            )
        except Exception as exc:
            raise self._driver_error(exc) from exc

    async def fill(
        self, session_id: str, *, fields: Sequence[tuple[str, str]], token: str
    ) -> Mapping[str, Any]:
        session = await self._require_session(session_id, token)
        if len(fields) > 128:
            raise CompanionError("LIMIT_EXCEEDED", "too many fields", status=413)
        for locator, value in fields:
            if not isinstance(locator, str) or not isinstance(value, str):
                raise CompanionError("INVALID_FIELDS", "fields must be strings")
            if len(value) > 10_000:
                raise CompanionError("LIMIT_EXCEEDED", "field value is too long", status=413)
        try:
            result = dict(await session.driver.fill(tuple(fields)))
        except Exception as exc:
            raise self._driver_error(exc) from exc
        session.fill_operations += 1
        return result

    async def find_text(
        self, session_id: str, *, query: str, token: str
    ) -> Mapping[str, Any]:
        session = await self._require_session(session_id, token)
        if not query or len(query) > 200:
            raise CompanionError("LIMIT_EXCEEDED", "text query is out of range", status=413)
        try:
            return dict(await session.driver.find_text(query))
        except Exception as exc:
            raise self._driver_error(exc) from exc

    async def collect_matches(
        self, session_id: str, *, pattern: str, limit: int, url: str, token: str
    ) -> Mapping[str, Any]:
        session = await self._require_session(session_id, token)
        try:
            return dict(
                await session.driver.collect_matches(
                    pattern=pattern, limit=limit, url=url
                )
            )
        except Exception as exc:
            raise self._driver_error(exc) from exc

    async def activate(
        self, session_id: str, *, locator: str, expected_path: str, token: str
    ) -> Mapping[str, Any]:
        session = await self._require_session(session_id, token)
        if not expected_path.startswith("/"):
            raise CompanionError(
                "NAVIGATION_TARGET_REQUIRED", "a static landing path is required", status=409
            )
        try:
            return dict(
                await session.driver.activate_navigation(
                    locator=locator, expected_path=expected_path
                )
            )
        except Exception as exc:
            raise self._driver_error(exc) from exc

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
        token: str,
    ) -> Mapping[str, Any]:
        session = await self._require_session(session_id, token)
        if session.fill_operations == 0:
            raise CompanionError(
                "FILL_REQUIRED", "a supervised fill must precede a submission", status=409
            )
        if not expected_method or not expected_origin or not expected_path.startswith("/"):
            raise CompanionError(
                "SUBMIT_TARGET_REQUIRED", "a bound submission target is required", status=409
            )
        try:
            return dict(
                await session.driver.click(
                    action_id=action_id,
                    locator=locator,
                    receipt_locator=receipt_locator,
                    expected_method=expected_method,
                    expected_origin=expected_origin,
                    expected_path=expected_path,
                )
            )
        except Exception as exc:
            raise self._driver_error(exc) from exc

    async def close(self, session_id: str, *, token: str) -> None:
        await self._finish(session_id, token, cancelled=False)

    async def cancel(self, session_id: str, *, token: str) -> None:
        await self._finish(session_id, token, cancelled=True)

    async def _finish(self, session_id: str, token: str, *, cancelled: bool) -> None:
        session = await self._require_session(session_id, token)
        session.revoked = True
        with contextlib.suppress(Exception):
            await session.driver.close()
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def revoke(self, *, session_id: str = "", token: str) -> None:
        if not session_id:
            self._require_root(token)
            for key in list(self._sessions):
                await self._close_raw(key)
            return
        session = self._sessions.get(session_id)
        if session is None:
            return
        if not self._token_matches(token, session.capability):
            self._require_root(token)
        await self._close_raw(session_id)

    async def diagnostics(self, *, token: str) -> Mapping[str, Any]:
        self._require_root(token)
        sessions = []
        for session in self._sessions.values():
            entry: dict[str, Any] = {
                "session_id": session.session_id,
                "extension_id": session.extension_id,
                "fill_operations": session.fill_operations,
            }
            with contextlib.suppress(Exception):
                entry["driver"] = dict(await session.driver.diagnostics())
            sessions.append(entry)
        return {
            "test_mode": self._test_mode,
            "session_count": len(self._sessions),
            "sessions": sessions,
        }

    async def shutdown(self) -> None:
        for key in list(self._sessions):
            await self._close_raw(key)

    async def _close_raw(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        session.revoked = True
        with contextlib.suppress(Exception):
            await session.driver.close()

    async def _require_session(self, session_id: str, token: str) -> CompanionSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise CompanionError("SESSION_NOT_FOUND", "unknown session", status=404)
        if session.revoked:
            raise CompanionError("SESSION_REVOKED", "the session was revoked", status=410)
        now = self._now()
        if session.expired(now):
            await self._close_raw(session_id)
            raise CompanionError("CAPABILITY_EXPIRED", "the capability has expired", status=401)
        if not self._token_matches(token, session.capability):
            raise CompanionError("CAPABILITY_INVALID", "invalid capability", status=401)
        return session

    def _require_root(self, token: str) -> None:
        if not self._token_matches(token, self._root_capability):
            raise CompanionError("CAPABILITY_INVALID", "invalid capability", status=401)

    @staticmethod
    def _token_matches(presented: str, expected: str) -> bool:
        import hmac

        return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))

    async def _sweep(self, now: float) -> None:
        expired = [
            key for key, session in self._sessions.items() if session.expired(now)
        ]
        for key in expired:
            await self._close_raw(key)

    @staticmethod
    def _driver_error(exc: Exception) -> CompanionError:
        code = str(getattr(exc, "code", "BROWSER_ERROR"))
        if code == "BROWSER_TIMEOUT":
            return CompanionError(code, "the browser command timed out", status=504)
        if code == "BROWSER_UNAVAILABLE":
            return CompanionError(code, "the browser is unavailable", status=503)
        return CompanionError(code, "the browser command failed", status=409)


__all__ = [
    "CAPABILITY_TTL_SECONDS",
    "MAX_COMPANION_SESSIONS",
    "CompanionError",
    "CompanionSession",
    "DesktopCompanion",
]
