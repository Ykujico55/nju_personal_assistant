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
from dataclasses import dataclass, field
from typing import Any

from personal_assistant.core.browser import evaluate_navigation

CAPABILITY_TTL_SECONDS = 600.0
MAX_COMPANION_SESSIONS = 4
MAX_SESSION_IDENTITIES = 65_536
MAX_SESSION_ID_LENGTH = 200


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
    origin_mode: str
    capability: str
    created_at: float
    expires_at: float
    driver: Any
    revoked: bool = False
    fill_operations: int = 0
    starting: bool = True
    expiry_task: asyncio.Task[None] | None = field(default=None, repr=False)
    close_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def expired(self, now: float) -> bool:
        return now >= self.expires_at

    def public(self) -> Mapping[str, Any]:
        return {
            "session_id": self.session_id,
            "purpose": self.purpose,
            "task_id": self.task_id,
            "extension_id": self.extension_id,
            "origin_mode": self.origin_mode,
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
        self._used_ids: set[str] = set()
        self._expiry_tasks: set[asyncio.Task[None]] = set()
        self._closing = False

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
        origin_mode: str = "allowlist",
        task_id: str,
        extension_id: str,
        token: str,
    ) -> Mapping[str, Any]:
        self._require_root(token)
        self._validate_session_id(session_id)
        origins = tuple(dict.fromkeys(str(item) for item in allowed_origins))
        if origin_mode not in {"allowlist", "open"}:
            raise CompanionError("ORIGIN_MODE_INVALID", "unknown browser origin mode")
        if origin_mode == "allowlist" and not origins:
            raise CompanionError("ORIGINS_REQUIRED", "at least one origin is required")
        now = self._now()
        await self._sweep(now)
        async with self._lock:
            if self._closing:
                raise CompanionError("COMPANION_STOPPING", "the companion is stopping", status=503)
            if session_id in self._used_ids and session_id not in self._sessions:
                raise CompanionError("SESSION_REVOKED", "session ids cannot be reused", status=410)
            if len(self._sessions) >= self._max_sessions:
                raise CompanionError("SESSION_LIMIT", "too many supervised sessions", status=429)
            if session_id in self._sessions:
                raise CompanionError("SESSION_EXISTS", "session id already exists", status=409)
            self._remember_session_id(session_id)
            driver = self._driver_factory(
                allowed_origins=origins, origin_mode=origin_mode, **self._driver_options
            )
            session = CompanionSession(
                session_id=session_id,
                purpose=purpose[:500],
                task_id=task_id[:200],
                extension_id=extension_id[:200],
                allowed_origins=origins,
                origin_mode=origin_mode,
                capability=secrets.token_urlsafe(32),
                created_at=now,
                expires_at=now + self._ttl,
                driver=driver,
            )
            self._sessions[session_id] = session
            expiry = asyncio.create_task(self._expire_session(session))
            session.expiry_task = expiry
            self._expiry_tasks.add(expiry)
            expiry.add_done_callback(self._expiry_tasks.discard)
        try:
            await driver.start()
        except BaseException:
            session.starting = False
            with contextlib.suppress(CompanionError):
                await self._close_raw(session_id, expected=session)
            raise
        session.starting = False
        if (
            session.revoked
            or session.expired(self._now())
            or self._closing
            or self._sessions.get(session_id) is not session
        ):
            await self._close_raw(session_id, expected=session)
            raise CompanionError("SESSION_REVOKED", "the creation was revoked", status=410)
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
        decision = evaluate_navigation(
            url,
            allowed_origins=set(session.allowed_origins),
            origin_mode=session.origin_mode,
        )
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

    async def adopt_opened_page(self, session_id: str, *, token: str) -> Mapping[str, Any]:
        session = await self._require_session(session_id, token)
        try:
            return dict(await session.driver.adopt_opened_page())
        except Exception as exc:
            raise self._driver_error(exc) from exc

    async def select_frame(
        self, session_id: str, *, origin: str, path: str, token: str
    ) -> Mapping[str, Any]:
        session = await self._require_session(session_id, token)
        decision = evaluate_navigation(
            origin + path,
            allowed_origins=set(session.allowed_origins),
            origin_mode=session.origin_mode,
        )
        if not decision.allowed or decision.origin != origin or decision.path != path:
            raise CompanionError("NAVIGATION_DENIED", decision.reason, status=403)
        try:
            return dict(await session.driver.select_frame(origin=origin, path=path))
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

    async def find_text(self, session_id: str, *, query: str, token: str) -> Mapping[str, Any]:
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
            return dict(await session.driver.collect_matches(pattern=pattern, limit=limit, url=url))
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
        expected_payload_sha256: str,
        expected_payload_locators: str,
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
                    expected_payload_sha256=expected_payload_sha256,
                    expected_payload_locators=expected_payload_locators,
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
        await self._close_raw(session_id, expected=session)

    async def revoke(self, *, session_id: str = "", token: str) -> None:
        self._require_root(token)
        if not session_id:
            for active_session in list(self._sessions.values()):
                await self._close_raw(active_session.session_id, expected=active_session)
            return
        self._validate_session_id(session_id)
        async with self._lock:
            # Also retire ids not registered yet: an HTTP request may arrive late.
            self._remember_session_id(session_id)
            session = self._sessions.get(session_id)
        if session is not None:
            await self._close_raw(session_id, expected=session)

    async def diagnostics(self, *, token: str) -> Mapping[str, Any]:
        self._require_root(token)
        sessions = []
        for session in list(self._sessions.values()):
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
        self._closing = True
        await self._await_disposal(asyncio.create_task(self._shutdown_sessions()))

    async def _shutdown_sessions(self) -> None:
        failed = False
        try:
            for session in list(self._sessions.values()):
                try:
                    await self._close_raw(session.session_id, expected=session)
                except CompanionError:
                    failed = True
        finally:
            timers = tuple(self._expiry_tasks)
            for task in timers:
                task.cancel()
            await asyncio.gather(*timers, return_exceptions=True)
        if failed:
            raise CompanionError(
                "SESSION_CLEANUP_FAILED", "session cleanup was not confirmed", status=503
            )

    @staticmethod
    async def _await_disposal(cleanup: asyncio.Task[None]) -> None:
        interrupted = False
        while True:
            try:
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                if cleanup.cancelled():
                    raise
                interrupted = True
            except Exception:
                if interrupted:
                    raise asyncio.CancelledError() from None
                raise
        if interrupted:
            raise asyncio.CancelledError()

    async def _close_raw(
        self, session_id: str, *, expected: CompanionSession | None = None
    ) -> None:
        async with self._lock:
            session = expected or self._sessions.get(session_id)
            if session is None:
                return
            session.revoked = True
        # Protect both resource disposal and identity removal from repeated
        # cancellation. A failed close keeps its record and expiry fallback.
        cleanup = asyncio.create_task(self._dispose_session(session, asyncio.current_task()))
        await self._await_disposal(cleanup)

    async def _dispose_session(
        self, session: CompanionSession, caller: asyncio.Task[Any] | None
    ) -> None:
        # A close before start completes may miss resources assigned later.
        # Serialize closes, and allow the post-start close even after removal.
        async with session.close_lock:
            try:
                await session.driver.close()
            except Exception:
                raise CompanionError(
                    "SESSION_CLEANUP_FAILED", "session cleanup was not confirmed", status=503
                ) from None
        async with self._lock:
            if not session.starting and self._sessions.get(session.session_id) is session:
                self._sessions.pop(session.session_id)
            expiry = session.expiry_task
            if expiry is not None and expiry is not caller:
                expiry.cancel()

    async def _expire_session(self, session: CompanionSession) -> None:
        while self._sessions.get(session.session_id) is session:
            remaining = session.expires_at - self._now()
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue
            # Failed cleanup remains registered and unavailable for an explicit
            # root retry. Do not log raw driver errors or claim it was closed.
            with contextlib.suppress(CompanionError):
                await self._close_raw(session.session_id, expected=session)
            return

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= MAX_SESSION_ID_LENGTH:
            raise CompanionError("SESSION_ID_INVALID", "session id is out of range")

    def _remember_session_id(self, session_id: str) -> None:
        if session_id in self._used_ids:
            return
        if len(self._used_ids) >= MAX_SESSION_IDENTITIES:
            raise CompanionError(
                "SESSION_HISTORY_LIMIT",
                "restart the companion before admitting new session ids",
                status=429,
            )
        self._used_ids.add(session_id)

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

    def require_root(self, token: str) -> None:
        """Public root-capability check (used before parsing request bodies)."""

        self._require_root(token)

    def _require_root(self, token: str) -> None:
        if not self._token_matches(token, self._root_capability):
            raise CompanionError("CAPABILITY_INVALID", "invalid capability", status=401)

    @staticmethod
    def _token_matches(presented: str, expected: str) -> bool:
        import hmac

        return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))

    async def _sweep(self, now: float) -> None:
        expired = [key for key, session in self._sessions.items() if session.expired(now)]
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
