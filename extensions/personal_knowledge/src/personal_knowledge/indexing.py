"""Configuration-triggered builds, serialized by one durable database lease."""

from __future__ import annotations

import asyncio
import copy
import uuid

from .index import KnowledgeIndex
from .models import ReconcileReport

BUILD_LEASE_KEY = "index.build.lease.v1"
LEASE_SECONDS = 60.0
RENEW_SECONDS = 15.0
WAIT_SECONDS = 120.0


class IndexBuildError(ValueError):
    pass


async def build_index(
    index: KnowledgeIndex, *, automatic: bool, mode: str = "full", poll_seconds: float = 2.0
) -> ReconcileReport | None:
    await index.ensure_ready()
    owner = uuid.uuid4().hex
    binding = index._binding_hash
    deadline = asyncio.get_running_loop().time() + WAIT_SECONDS

    state = "FAILED"
    scan: asyncio.Task[ReconcileReport] | None = None
    keeper: asyncio.Task[None] | None = None
    cancelled: asyncio.CancelledError | None = None
    claim: asyncio.Task[bool] | None = None

    async def renew() -> None:
        while True:
            await asyncio.sleep(RENEW_SECONDS)
            if not await index.store.renew_build(owner, LEASE_SECONDS):
                raise IndexBuildError("INDEX_LEASE_LOST")

    try:
        while True:
            if automatic and (await index.index_status())["index_state"] == "READY":
                return None
            claim = asyncio.create_task(index.store.claim_build(
                owner, binding, LEASE_SECONDS, retry_failed=not automatic,
            ))
            # Caller cancellation must not discard an already-issued host request.
            if await asyncio.shield(claim):
                break
            previous = await index.store.get_meta(BUILD_LEASE_KEY)
            if (
                automatic
                and previous
                and previous.get("binding_hash") == binding
                and previous.get("state") == "FAILED"
            ):
                raise IndexBuildError("INDEX_BUILD_FAILED")
            if asyncio.get_running_loop().time() >= deadline:
                raise IndexBuildError("INDEX_BUILD_BUSY")
            await asyncio.sleep(poll_seconds)
        # A waiting replica must recheck after claiming; another may have completed.
        if automatic and (await index.index_status())["index_state"] == "READY":
            state = "DONE"
            return None
        scoped = copy.copy(index)
        scoped.store = index.store.with_build_lease(owner)
        scan = asyncio.create_task(scoped.reconcile(mode=mode, lease_owner=owner))
        keeper = asyncio.create_task(renew())
        done, _ = await asyncio.wait((scan, keeper), return_when=asyncio.FIRST_COMPLETED)
        if keeper in done:
            keeper.result()
        report = await scan
        if (await index.index_status())["index_state"] != "READY":
            raise IndexBuildError("INDEX_BUILD_FAILED")
        state = "DONE"
        return report
    except asyncio.CancelledError as exc:
        state = "CANCELLED"
        cancelled = exc
        raise
    except IndexBuildError:
        raise
    except Exception:
        raise IndexBuildError("INDEX_BUILD_FAILED") from None
    finally:
        async def cleanup() -> bool:
            tasks = [task for task in (scan, keeper) if task is not None]
            for task in tasks:
                task.cancel()
            # The scan joins any in-flight file I/O before its cancellation ends.
            # The owner must not be released until that entire join has finished.
            await asyncio.gather(*tasks, return_exceptions=True)
            if claim is None:
                return False  # No acquisition request was issued.
            claim_error: BaseException | None = None
            try:
                acquired = await _resolve_claim(index, claim, owner, poll_seconds)
            except BaseException as exc:
                # A failed response is not proof that the host rolled back.
                # Keep the error and attempt owner-CAS cleanup on the same channel.
                acquired, claim_error = True, exc
            try:
                finished = await index.store.finish_build(owner, state) if acquired else False
                if state == "DONE" and not finished:
                    raise IndexBuildError("INDEX_LEASE_LOST")
            finally:
                # A pending reply is abandoned only after a committed owner was
                # read back and its CAS cleanup was attempted. All probe requests
                # have completed; never cancel an outstanding verification RPC.
                abandoned_reply = not claim.done()
                if abandoned_reply:
                    claim.cancel()
                (claim_result,) = await asyncio.gather(claim, return_exceptions=True)
                if (
                    claim_error is None
                    and isinstance(claim_result, BaseException)
                    and not (
                        abandoned_reply and isinstance(claim_result, asyncio.CancelledError)
                    )
                ):
                    claim_error = claim_result
            if claim_error is not None:
                raise claim_error
            return finished

        # Protect the whole cleanup unit, including the owner-CAS RPC. Shielding
        # only scan I/O is insufficient: a later cancel can skip finish_build.
        cleanup_task = asyncio.create_task(cleanup())
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError as exc:
                if cancelled is None:
                    cancelled = exc
            except BaseException:
                break  # Consume and propagate the cleanup result below.
        try:
            cleanup_task.result()
        except BaseException as exc:
            if cancelled is not None:
                # Cancellation remains visible, without losing cleanup failure.
                raise cancelled from exc
            raise
        if cancelled is not None:
            raise cancelled


async def _resolve_claim(
    index: KnowledgeIndex, claim: asyncio.Task[bool], owner: str, poll_seconds: float,
) -> bool:
    """Resolve acquisition without mistaking an undelivered reply for rollback.

    A read-back of this build's unique owner proves the claim committed. Without
    that evidence, wait for the issued request; a missing/different owner alone
    does not rule out a late commit. The production host processes one Worker's
    requests in order, so a completed follow-up read also follows its claim RPC.
    """
    while not claim.done():
        try:
            lease = await index.store.get_meta(BUILD_LEASE_KEY)
        except Exception:
            # Do not abandon a request just because the verification read failed.
            # The SDK's existing deadline/error still governs its completion.
            return await claim
        if lease and lease.get("owner") == owner:
            return True
        # This waits for completion without cancelling the claim on a poll tick.
        await asyncio.wait((claim,), timeout=poll_seconds)
    return claim.result()
