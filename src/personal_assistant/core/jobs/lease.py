"""Background lease renewal for long model, browser and extension calls."""

from __future__ import annotations

import asyncio
import contextlib

from .queue import JobQueuePort, LeaseConflict


class LeaseLost(RuntimeError):
    pass


class LeaseKeepalive:
    def __init__(
        self,
        queue: JobQueuePort,
        *,
        job_id: str,
        worker_id: str,
        lease_seconds: int = 30,
        heartbeat_seconds: float | None = None,
    ) -> None:
        self._queue = queue
        self._job_id = job_id
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds or max(1.0, lease_seconds / 3)
        if self._heartbeat_seconds >= lease_seconds:
            raise ValueError("heartbeat interval must be shorter than the lease")
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._failure: BaseException | None = None

    async def __aenter__(self) -> LeaseKeepalive:
        self._task = asyncio.create_task(self._run(), name=f"lease:{self._job_id}")
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        with contextlib.suppress(LeaseConflict):
            await self._queue.release(job_id=self._job_id, worker_id=self._worker_id)
        self.ensure_owned()

    def ensure_owned(self) -> None:
        if self._failure is not None:
            raise LeaseLost(f"lease renewal failed for {self._job_id}") from self._failure

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._heartbeat_seconds
                    )
                    return
                except TimeoutError:
                    await self._queue.heartbeat(
                        job_id=self._job_id,
                        worker_id=self._worker_id,
                        lease_seconds=self._lease_seconds,
                    )
        except BaseException as exc:
            if not isinstance(exc, asyncio.CancelledError):
                self._failure = exc
            raise

