"""Async cleanup helpers shared by the RPC client, installer and lifecycle.

Cancellation must terminate subprocesses and remove partially installed code
before the original ``CancelledError`` is re-raised.  ``shield_cleanup`` keeps
the cleanup running to completion even if the surrounding task is cancelled
again, which is exactly the guarantee those boundaries need.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable


async def run_blocking[T](func: Callable[..., T], /, *args: object) -> T:
    """Run a blocking function in a thread and make cancellation safe.

    Only the awaiting future is cancellable, so on cancellation this waits for
    the worker thread to actually finish before the ``CancelledError`` is
    re-raised.  Cleanup that runs afterwards can therefore delete the
    destination without a still-running thread recreating files behind it.
    The first cancellation always wins: a thread that fails while the caller is
    being cancelled still observes the cancellation, never its own exception.
    """

    task = asyncio.ensure_future(asyncio.to_thread(func, *args))
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            # Remember the first cancellation; repeated cancellations keep
            # waiting.  The thread result is consumed either way so a late
            # failure can never replace the cancellation the caller saw.
            if cancelled is None:
                cancelled = exc
            continue
        except BaseException:
            if cancelled is None:
                raise
            # The thread failed after a cancellation was observed; the
            # cancellation still wins and the thread result is swallowed.
            break
    if cancelled is not None:
        with contextlib.suppress(BaseException):
            task.result()
        raise cancelled
    return task.result()


async def shield_cleanup[T](awaitable: Awaitable[T]) -> T:
    """Run cleanup to completion despite repeated cancellation, then return."""

    task = asyncio.ensure_future(awaitable)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # A second cancellation arrived while cleaning up; keep waiting.
            continue
    return task.result()


async def terminate_process(
    process: asyncio.subprocess.Process, *, grace_seconds: float = 2.0
) -> None:
    """Terminate, then kill, and always reap the child process."""

    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), grace_seconds)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.wait()
    else:
        with contextlib.suppress(Exception):
            await process.wait()


__all__ = ["run_blocking", "shield_cleanup", "terminate_process"]
