"""Cancellation-resistant cleanup for model transports and providers.

Closing a response or client is a resource guarantee: a second cancellation
must not be able to interrupt it.  ``run_cleanup`` awaits the cleanup task to
completion and only then re-raises the cancellation, so callers never leave a
half-closed stream behind.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable


async def run_cleanup[T](awaitable: Awaitable[T]) -> T:
    """Run cleanup to completion despite repeated cancellation."""

    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            continue
    results: list[T] = []
    error: BaseException | None = None
    try:
        results.append(task.result())
    except asyncio.CancelledError:
        cancelled = True
    except Exception as exc:  # noqa: BLE001 - reported after the cleanup completes
        error = exc
    if cancelled:
        raise asyncio.CancelledError()
    if error is not None:
        raise error
    return results[0]


__all__ = ["run_cleanup"]
