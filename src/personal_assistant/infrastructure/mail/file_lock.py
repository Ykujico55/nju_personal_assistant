"""Small cross-process exclusive file lock.

Used to serialize host account-registry writes (Admin process) against the
SMTP DATA critical section (public/worker process).  Exclusive-only semantics
are sufficient: writers hold the lock for the whole read-modify-write, and the
sender holds it across the already-started DATA submission so a registry change
either completes first (send aborts) or waits for the committed send.
"""

from __future__ import annotations

import importlib
import os
import time
from pathlib import Path
from types import TracebackType

_LOCK_BYTES = 1
_RETRY_SECONDS = 0.01


class ExclusiveFileLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, timeout_seconds: float = 5.0) -> bool:
        if self._fd is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        while True:
            if _try_lock(fd):
                self._fd = fd
                return True
            if time.monotonic() >= deadline:
                os.close(fd)
                return False
            time.sleep(_RETRY_SECONDS)

    def release(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> ExclusiveFileLock:
        if not self.acquire():
            raise TimeoutError("could not acquire the registry lock")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def _try_lock(fd: int) -> bool:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")
            msvcrt.locking(fd, msvcrt.LK_NBLCK, _LOCK_BYTES)
        else:
            fcntl = importlib.import_module("fcntl")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")
            msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCK_BYTES)
        else:
            fcntl = importlib.import_module("fcntl")
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        return


__all__ = ["ExclusiveFileLock"]
