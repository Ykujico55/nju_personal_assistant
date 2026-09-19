"""Process-local coordination and account dispatch guards.

- ``MailExecutionOwnerRegistry`` tracks live dispatch owner incarnations so the
  ledger recovery sweep never turns a slow-but-alive sender's ``EXECUTING`` row
  into ``UNKNOWN``.
- ``MailAccountDispatchGuard`` is a *live*, fail-closed guard for the account
  binding used by an in-flight dispatch.  ``valid`` re-reads the registry every
  time (no polling cache), a read failure invalidates the guard, and
  ``begin_critical_section`` acquires the same cross-process lock that account
  writers take, so the account check and entering DATA are atomic:
  a registry change either completes before the section (send aborts) or waits
  until the already-started DATA submission is committed.
- ``CompositeMailGuard`` combines the ledger lease guard and the account guard.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from personal_assistant.core.mail import MailSendLeaseGuard


class _LockLike(Protocol):
    def acquire(self, timeout_seconds: float = 5.0) -> bool: ...

    def release(self) -> None: ...


class _ThreadLockAdapter:
    """Adapts ``threading.RLock`` to the lock-like acquire/release protocol."""

    def __init__(self, lock: threading.RLock) -> None:
        self._lock = lock

    def acquire(self, timeout_seconds: float = 5.0) -> bool:
        return bool(self._lock.acquire(timeout=max(timeout_seconds, 0.0)))

    def release(self) -> None:
        self._lock.release()


class MailExecutionOwnerRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners: set[str] = set()

    def register(self, owner_id: str) -> None:
        with self._lock:
            self._owners.add(owner_id)

    def unregister(self, owner_id: str) -> None:
        with self._lock:
            self._owners.discard(owner_id)

    def active(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._owners)


class MailAccountDispatchGuard(MailSendLeaseGuard):
    """Live account-binding guard backed by the registry itself."""

    REASON = "ACCOUNT_CHANGED"

    def __init__(
        self,
        verifier: Callable[[], bool],
        lock: _LockLike | None = None,
    ) -> None:
        self._verifier = verifier
        self._lock = lock
        self._valid = True
        self._held = False

    @property
    def valid(self) -> bool:
        if not self._valid:
            return False
        try:
            if self._verifier():
                return True
        except Exception:  # noqa: BLE001 - cannot confirm the binding: fail closed
            pass
        self._valid = False
        return False

    @property
    def reason(self) -> str | None:
        return self.REASON if not self.valid else None

    def invalidate(self) -> None:
        self._valid = False

    def begin_critical_section(self, timeout_seconds: float = 5.0) -> bool:
        if self._lock is not None:
            if not self._lock.acquire(timeout_seconds):
                self._valid = False
                return False
            self._held = True
        if not self.valid:
            self.end_critical_section()
            return False
        return True

    def end_critical_section(self) -> None:
        if self._held and self._lock is not None:
            self._lock.release()
        self._held = False


class CompositeMailGuard(MailSendLeaseGuard):
    """Valid only while every underlying guard is valid."""

    def __init__(self, *guards: MailSendLeaseGuard | None) -> None:
        self._guards = tuple(guard for guard in guards if guard is not None)
        self._held: list[MailSendLeaseGuard] = []

    @property
    def valid(self) -> bool:
        return all(guard.valid for guard in self._guards)

    @property
    def reason(self) -> str | None:
        for guard in self._guards:
            if guard.valid:
                continue
            return getattr(guard, "reason", None)
        return None

    def begin_critical_section(self, timeout_seconds: float = 5.0) -> bool:
        """Acquire every guard's critical section, then re-verify all guards.

        Account locks are acquired before the (lock-free) lease guard, the
        per-guard full timeout is replaced by one total monotonic deadline, and
        every guard is re-validated only after all locks are held, so a lease
        that expires while waiting for the account lock cannot enter DATA.
        """

        self._held = []
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        success = True
        for guard in reversed(self._guards):
            begin = getattr(guard, "begin_critical_section", None)
            if begin is None:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0.0 or not begin(remaining):
                success = False
                break
            self._held.append(guard)
        if success:
            success = all(guard.valid for guard in self._guards)
        if not success:
            self.end_critical_section()
        return success

    def end_critical_section(self) -> None:
        for guard in reversed(self._held):
            end = getattr(guard, "end_critical_section", None)
            if end is not None:
                end()
        self._held = []


__all__ = [
    "CompositeMailGuard",
    "MailAccountDispatchGuard",
    "MailExecutionOwnerRegistry",
]
