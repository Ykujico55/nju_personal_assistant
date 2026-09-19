"""Host-side executor for mail-send tools behind the Tool Gateway.

The executor never sends what a worker asks for: it resolves the account from
the host-owned registry (the extension may only submit ``account_id`` and the
host-computed fingerprint), asks the owning extension to materialize the
*current* draft version, verifies the resulting bytes against the approved
payload hash and envelope, durably records PREPARED/EXECUTING in the transport
ledger, and only then opens SMTP.  A stale draft, hash mismatch, unavailable
artifact, unregistered account or policy denial fails definitively before any
SMTP connection is attempted.  A ledger failure after submission is reported as
UNKNOWN, never as success.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.core.mail import (
    MailAccountNotFoundError,
    MailAccountRecord,
    MailAccountRegistry,
    MailDeliveryLedger,
    MailDeliveryReceipt,
    MailDeliveryRecord,
    MailDeliveryRequest,
    MailDeliveryStatus,
    MailError,
    MailErrorCode,
    MailLedgerConflictError,
    MailLedgerStateError,
    MailPolicy,
    MailSendLeaseGuard,
    MailTransportBroker,
)
from personal_assistant.core.tools.gateway import (
    DefinitiveToolFailure,
    OutcomeUnknownError,
    ToolExecutionContext,
    UserActionRequiredError,
)
from personal_assistant.domain.models import ToolDescriptor

from .mime import parse_envelope
from .owners import MailExecutionOwnerRegistry

MAIL_SEND_CAPABILITY = "mail.send"
DEFAULT_CALL_DEADLINE_SECONDS = 120.0


class MailArtifactReader(Protocol):
    async def read_as_host(self, artifact_id: str) -> bytes: ...


class ExtensionToolInvoker(Protocol):
    async def invoke_tool(
        self,
        extension_id: str,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        task_id: str,
        run_id: str,
        idempotency_key: str,
        deadline_seconds: float,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class _Expectations:
    local_action_id: str
    account_id: str
    account_fingerprint: str
    draft_id: str
    draft_version: int
    message_id: str
    from_address: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    bcc: tuple[str, ...]
    subject: str
    mime_sha256: str
    attachment_hashes: tuple[str, ...]


class MailSendExecutor:
    """A ``ToolExecutor`` for descriptors that require the ``mail.send`` grant."""

    def __init__(
        self,
        *,
        broker: MailTransportBroker,
        artifacts: MailArtifactReader,
        invoker: ExtensionToolInvoker,
        ledger: MailDeliveryLedger,
        accounts: MailAccountRegistry,
        policy: MailPolicy | None = None,
        call_deadline_seconds: float = DEFAULT_CALL_DEADLINE_SECONDS,
        owners: MailExecutionOwnerRegistry | None = None,
        lease_seconds: float | None = None,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self._broker = broker
        self._artifacts = artifacts
        self._invoker = invoker
        self._ledger = ledger
        self._accounts = accounts
        self._owners = owners
        self._policy = policy
        self._call_deadline = call_deadline_seconds
        self._lease_override = lease_seconds
        self._heartbeat_override = heartbeat_interval_seconds
        # Stable per-executor owner: lets the ledger distinguish a live dispatch
        # from a crashed one without a second caller burning the first.
        self._owner_id = f"mail-send-{uuid4().hex}"

    async def execute(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        if MAIL_SEND_CAPABILITY not in descriptor.required_capabilities:
            raise DefinitiveToolFailure(
                "this executor only handles tools that declare the send capability"
            )
        expectations = _expectations(arguments, context)
        account = await self._resolve_account(expectations)
        if account.address.lower() != expectations.from_address.lower():
            raise DefinitiveToolFailure("the sender does not match the registered account")
        if self._policy is not None:
            for recipient in (*expectations.to, *expectations.cc, *expectations.bcc):
                if self._policy.recipient_allowed(recipient):
                    continue
                raise DefinitiveToolFailure(
                    "recipient is not on the controlled test allowlist"
                )
        materialized = await self._materialize(descriptor, arguments, context)
        mime_bytes = await self._verify_bytes(materialized, expectations)
        _verify_envelope(mime_bytes, materialized, expectations)
        # Re-resolve after the extension await: the user may have disabled,
        # re-bound or deleted the account while materialization was running.
        account = await self._reconfirm_account(expectations)
        if not account.send_enabled:
            raise DefinitiveToolFailure("sending is not enabled for this account")
        if not self._broker.send_available:
            raise DefinitiveToolFailure("mail sending is disabled by host policy")
        envelope_digest = canonical_sha256(dict(arguments))
        record = MailDeliveryRecord(
            local_action_id=expectations.local_action_id,
            account_id=account.account_id,
            message_id=expectations.message_id,
            status=MailDeliveryStatus.PREPARED,
            envelope_digest=envelope_digest,
            mime_sha256=expectations.mime_sha256,
        )
        state = await self._prepare(record)
        if state.terminal:
            # The same local action id already reached a terminal state: this is
            # an idempotent replay and must not dispatch again.
            return _replay_view(expectations, materialized, state)
        if state is MailDeliveryStatus.EXECUTING:
            raise OutcomeUnknownError(
                expectations.local_action_id,
                "a previous dispatch attempt was interrupted; outcome is unknown",
            )
        # One owner incarnation is used for the ledger claim, the live-owner
        # registry, the heartbeat and unregister; they must be identical or the
        # heartbeat can never renew the row it claimed.
        owner_id = f"{self._owner_id}-{uuid4().hex}"
        # The ledger grants the lease inside ``_begin``; the local guard must
        # share that origin instead of restarting a full lease afterwards, or
        # the guard would outlive the database lease by the post-claim work.
        claim_started = time.monotonic()
        state = await self._begin(
            expectations.local_action_id, envelope_digest, owner_id
        )
        if state.terminal:
            return _replay_view(expectations, materialized, state)
        if state is not MailDeliveryStatus.EXECUTING:
            raise OutcomeUnknownError(
                expectations.local_action_id, "the transport ledger is not executable"
            )
        # Final pre-connect check: no account change may slip between the
        # durable EXECUTING claim and the SMTP connection.
        account = await self._reconfirm_account(expectations)
        if not account.send_enabled:
            await self._finalize_failure(
                expectations.local_action_id,
                envelope_digest,
                diagnostic_code=MailErrorCode.ACCOUNT_DISABLED.value,
            )
            raise DefinitiveToolFailure("sending was disabled before dispatch")
        request = MailDeliveryRequest(
            local_action_id=expectations.local_action_id,
            message_id=expectations.message_id,
            from_address=expectations.from_address,
            to=expectations.to,
            cc=expectations.cc,
            bcc=expectations.bcc,
            subject=expectations.subject,
            mime_bytes=mime_bytes,
        )
        guard = _LeaseGuard(self._lease_seconds(), started_at=claim_started)
        if not guard.valid:
            # Post-claim work outlived the ledger lease: never open SMTP and
            # leave the row to lease recovery / Sent reconciliation.
            raise OutcomeUnknownError(
                expectations.local_action_id,
                "the transport lease expired before dispatch",
            )
        if self._owners is not None:
            self._owners.register(owner_id)
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(expectations.local_action_id, owner_id, guard)
        )
        try:
            receipt = await self._broker.send(
                account.account_id, request, guard=guard
            )
        except MailError as exc:
            # The broker only raises before submission (policy/credential/TLS at
            # connect); record the definitive failure and classify for the gateway.
            await self._finalize_failure(
                expectations.local_action_id,
                envelope_digest,
                diagnostic_code=exc.code.value,
            )
            if exc.needs_user_action:
                raise UserActionRequiredError(
                    "the mail account needs user action before sending"
                ) from None
            raise DefinitiveToolFailure(
                "the mail transport refused the message before submission"
            ) from None
        finally:
            heartbeat.cancel()
            await _drain_task(heartbeat)
            if self._owners is not None:
                self._owners.unregister(owner_id)
        return await self._complete(expectations, receipt, envelope_digest, materialized)

    async def _resolve_account(self, expectations: _Expectations) -> MailAccountRecord:
        try:
            account = await self._accounts.resolve(expectations.account_id)
        except MailAccountNotFoundError:
            raise DefinitiveToolFailure("the account is not registered with the host") from None
        if account.fingerprint() != expectations.account_fingerprint:
            raise DefinitiveToolFailure(
                "the account binding does not match the host registry"
            )
        return account

    async def _prepare(self, record: MailDeliveryRecord) -> MailDeliveryStatus:
        try:
            return await self._ledger.prepare(record)
        except MailLedgerConflictError:
            raise DefinitiveToolFailure(
                "the local action id is already bound to a different envelope"
            ) from None
        except MailLedgerStateError:
            raise DefinitiveToolFailure("the transport ledger rejected the dispatch") from None
        except Exception:  # noqa: BLE001 - ledger unavailable must not send
            raise OutcomeUnknownError(
                record.local_action_id, "the transport ledger is unavailable"
            ) from None

    async def _reconfirm_account(self, expectations: _Expectations) -> MailAccountRecord:
        try:
            account = await self._accounts.resolve(expectations.account_id)
        except MailAccountNotFoundError:
            raise DefinitiveToolFailure("the account was removed from the host registry") from None
        if account.fingerprint() != expectations.account_fingerprint:
            raise DefinitiveToolFailure(
                "the account binding changed after this action was approved"
            )
        return account

    async def _begin(
        self, local_action_id: str, envelope_digest: str, owner_id: str
    ) -> MailDeliveryStatus:
        try:
            return await self._ledger.begin_execution(
                local_action_id,
                envelope_digest,
                owner_id=owner_id,
                lease_seconds=self._lease_seconds(),
            )
        except Exception:  # noqa: BLE001 - cannot dispatch without a durable EXECUTING row
            raise OutcomeUnknownError(
                local_action_id, "the transport ledger could not record dispatch"
            ) from None

    def _lease_seconds(self) -> float:
        if self._lease_override is not None:
            return max(float(self._lease_override), 0.5)
        return min(max(self._call_deadline * 2.0, 120.0), 3600.0)

    def _heartbeat_interval(self) -> float:
        if self._heartbeat_override is not None:
            return max(float(self._heartbeat_override), 0.05)
        return max(self._lease_seconds() / 3.0, 5.0)

    async def _heartbeat_loop(
        self, local_action_id: str, owner_id: str, guard: _LeaseGuard
    ) -> None:
        """Renew the ledger lease while the transport is in flight.

        The guard tracks its own monotonic deadline, so the lease expires even
        if ``ledger.heartbeat`` hangs forever.  Every heartbeat call is bounded
        by the remaining local lease, a transient database error is tolerated
        only until the deadline, and losing the row/owner invalidates
        immediately.
        """

        loop = asyncio.get_running_loop()
        lease = self._lease_seconds()
        while True:
            await asyncio.sleep(self._heartbeat_interval())
            remaining = guard.remaining_seconds()
            if remaining <= 0.0:
                guard.invalidate()
                return
            try:
                attempt_started = time.monotonic()
                async with asyncio.timeout_at(loop.time() + remaining):
                    renewed = await self._ledger.heartbeat(
                        local_action_id, owner_id, lease_seconds=lease
                    )
            except TimeoutError:
                # The database call outlived the local lease: fail closed.
                guard.invalidate()
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - tolerate only until the deadline
                if guard.remaining_seconds() <= 0.0:
                    guard.invalidate()
                    return
                continue
            if not renewed:
                guard.invalidate()
                return
            guard.renew(lease, started_at=attempt_started)

    async def _finalize_failure(
        self,
        local_action_id: str,
        envelope_digest: str,
        diagnostic_code: str = "PRE_SUBMISSION_FAILURE",
    ) -> None:
        try:
            await self._ledger.finalize(
                local_action_id,
                envelope_digest,
                status=MailDeliveryStatus.FAILED,
                diagnostic_code=diagnostic_code,
            )
        except Exception:  # noqa: BLE001 - no external side effect happened
            return

    async def _complete(
        self,
        expectations: _Expectations,
        receipt: MailDeliveryReceipt,
        envelope_digest: str,
        materialized: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        try:
            final = await self._ledger.finalize(
                receipt.local_action_id,
                envelope_digest,
                status=receipt.status,
                recipient_results=receipt.recipient_results,
                server_code=receipt.server_code,
            )
        except Exception:  # noqa: BLE001 - submission happened; never claim success
            raise OutcomeUnknownError(
                receipt.local_action_id,
                "the transport result could not be recorded; outcome is unknown",
            ) from None
        if final.terminal and final is not receipt.status:
            raise OutcomeUnknownError(
                receipt.local_action_id,
                "the transport ledger already held a different terminal state",
            )
        if receipt.status is MailDeliveryStatus.UNKNOWN:
            raise OutcomeUnknownError(
                receipt.local_action_id,
                "the mail transport could not confirm acceptance; outcome is unknown",
            )
        if receipt.status is MailDeliveryStatus.PARTIAL:
            raise DefinitiveToolFailure(
                "the mail transport accepted only part of the recipient list"
            )
        if receipt.status is MailDeliveryStatus.FAILED:
            raise DefinitiveToolFailure(
                "the mail transport rejected the message before delivery"
            )
        return _view(receipt, final, expectations, materialized)

    async def _materialize(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        try:
            result = await self._invoker.invoke_tool(
                descriptor.extension_id,
                descriptor.id,
                arguments,
                task_id=context.task_id,
                run_id=context.attempt_id,
                idempotency_key=context.idempotency_key,
                deadline_seconds=self._call_deadline,
            )
        except Exception:  # noqa: BLE001 - extension failure is definitive, no send
            raise DefinitiveToolFailure(
                "the owning extension could not materialize the approved draft"
            ) from None
        outcome = result.get("outcome")
        if outcome == "NEEDS_USER_ACTION":
            raise UserActionRequiredError(
                "the extension requires user action before this send"
            )
        if outcome != "SUCCEEDED":
            raise DefinitiveToolFailure(
                "the extension refused to materialize the approved draft version"
            )
        output = result.get("output")
        if not isinstance(output, Mapping):
            raise DefinitiveToolFailure("the extension returned no send material")
        return output

    async def _verify_bytes(
        self, materialized: Mapping[str, Any], expectations: _Expectations
    ) -> bytes:
        artifact_id = materialized.get("mime_artifact_id")
        mime_sha256 = materialized.get("mime_sha256")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise DefinitiveToolFailure("the extension returned no message artifact")
        if mime_sha256 != expectations.mime_sha256:
            raise DefinitiveToolFailure(
                "the materialized draft version does not match the approved snapshot"
            )
        try:
            data = await self._artifacts.read_as_host(artifact_id)
        except Exception:  # noqa: BLE001 - missing artifact is definitive
            raise DefinitiveToolFailure(
                "the approved message artifact is unavailable"
            ) from None
        if hashlib.sha256(data).hexdigest() != expectations.mime_sha256:
            raise DefinitiveToolFailure(
                "the approved message bytes failed integrity verification"
            )
        return data


class _LeaseGuard(MailSendLeaseGuard):
    """Monotonic-deadline lease guard shared with the SMTP worker thread.

    ``valid`` is a live deadline check, so a hung heartbeat can never keep the
    guard alive past the local lease.  The deadline is anchored to
    ``started_at`` (the moment the database claim or renewal was issued), so a
    guard created after slow post-claim work does not receive a fresh full
    lease.  ``invalidate``/``renew`` are serialized by a thread lock so the
    transport never observes a torn state.  The critical-section hooks are
    no-ops because the ledger lease has no cross-process lock.
    """

    REASON = "EXECUTION_LEASE_LOST"

    def __init__(self, lease_seconds: float, *, started_at: float | None = None) -> None:
        self._lock = threading.Lock()
        self._valid = True
        origin = time.monotonic() if started_at is None else started_at
        self._expires_at = origin + max(float(lease_seconds), 0.0)

    @property
    def valid(self) -> bool:
        with self._lock:
            return self._valid and time.monotonic() < self._expires_at

    @property
    def reason(self) -> str | None:
        return self.REASON if not self.valid else None

    def invalidate(self) -> None:
        with self._lock:
            self._valid = False

    def renew(self, lease_seconds: float, *, started_at: float | None = None) -> None:
        with self._lock:
            if not self._valid:
                return
            origin = time.monotonic() if started_at is None else started_at
            if time.monotonic() >= self._expires_at:
                # Already expired: the ledger may have recovered the row, so a
                # late renewal must not resurrect the dispatch.
                return
            self._expires_at = origin + max(float(lease_seconds), 0.0)

    def remaining_seconds(self) -> float:
        with self._lock:
            if not self._valid:
                return 0.0
            return max(0.0, self._expires_at - time.monotonic())

    def begin_critical_section(self, timeout_seconds: float = 5.0) -> bool:
        del timeout_seconds
        return self.valid

    def end_critical_section(self) -> None:
        return None


async def _drain_task(task: asyncio.Task[Any]) -> None:
    """Cancel a helper task and wait for it without corrupting cancellation."""

    task.cancel()
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
            continue
        except Exception:  # noqa: BLE001 - helper errors are irrelevant here
            break
    with contextlib.suppress(Exception, asyncio.CancelledError):
        task.exception()


def _expectations(
    arguments: Mapping[str, Any], context: ToolExecutionContext
) -> _Expectations:
    try:
        local_action_id = _text(arguments.get("local_action_id"))
        account_id = _text(arguments.get("account_id"))
        account_fingerprint = _digest(arguments.get("account_fingerprint"))
        draft_id = _text(arguments.get("draft_id"))
        draft_version = arguments.get("draft_version")
        if (
            isinstance(draft_version, bool)
            or not isinstance(draft_version, int)
            or draft_version < 1
        ):
            raise ValueError("draft_version is invalid")
        message_id = _text(arguments.get("message_id"))
        from_address = _text(arguments.get("from_address"))
        to = _addresses(arguments.get("to"))
        cc = _addresses(arguments.get("cc", []))
        bcc = _addresses(arguments.get("bcc", []))
        subject = _text(arguments.get("subject"), allow_empty=True)
        mime_sha256 = _digest(arguments.get("mime_sha256"))
        attachment_hashes = _digests(arguments.get("attachment_hashes", []))
    except ValueError as exc:
        raise DefinitiveToolFailure(f"invalid send arguments: {exc}") from None
    if context.idempotency_key != local_action_id:
        raise DefinitiveToolFailure(
            "the local action id must equal the gateway idempotency key"
        )
    if not to and not cc and not bcc:
        raise DefinitiveToolFailure("at least one recipient is required")
    return _Expectations(
        local_action_id=local_action_id,
        account_id=account_id,
        account_fingerprint=account_fingerprint,
        draft_id=draft_id,
        draft_version=draft_version,
        message_id=message_id,
        from_address=from_address,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        mime_sha256=mime_sha256,
        attachment_hashes=attachment_hashes,
    )


def _verify_envelope(
    mime_bytes: bytes,
    materialized: Mapping[str, Any],
    expectations: _Expectations,
) -> None:
    actual = parse_envelope(mime_bytes)
    if actual.get("message_id") != expectations.message_id:
        raise DefinitiveToolFailure("the message id does not match the approval")
    if str(actual.get("subject") or "") != expectations.subject:
        raise DefinitiveToolFailure("the subject does not match the approval")
    if str(actual.get("from_address") or "").lower() != expectations.from_address.lower():
        raise DefinitiveToolFailure("the sender does not match the approval")
    if _normalized(actual.get("to_addresses")) != _normalized(expectations.to):
        raise DefinitiveToolFailure("the To recipients do not match the approval")
    if _normalized(actual.get("cc_addresses")) != _normalized(expectations.cc):
        raise DefinitiveToolFailure("the Cc recipients do not match the approval")
    materialized_bcc = materialized.get("bcc")
    try:
        extension_bcc = _addresses(materialized_bcc if materialized_bcc is not None else [])
    except ValueError:
        raise DefinitiveToolFailure("the materialized Bcc list is invalid") from None
    if _normalized(list(extension_bcc)) != _normalized(expectations.bcc):
        raise DefinitiveToolFailure("the Bcc recipients do not match the approval")
    try:
        extension_hashes = _digests(materialized.get("attachment_hashes", []))
    except ValueError:
        raise DefinitiveToolFailure("the materialized attachment list is invalid") from None
    mime_hashes = _normalized(actual.get("attachment_hashes"))
    if (
        set(extension_hashes) != set(expectations.attachment_hashes)
        or mime_hashes != set(expectations.attachment_hashes)
    ):
        raise DefinitiveToolFailure("the attachment hashes do not match the approval")


def _view(
    receipt: MailDeliveryReceipt,
    ledger_status: MailDeliveryStatus,
    expectations: _Expectations,
    materialized: Mapping[str, Any],
) -> Mapping[str, Any]:
    return {
        # The tool output schema declares the draft materialization fields; keep
        # them on every path so the gateway can validate one stable shape.
        "draft_id": expectations.draft_id,
        "draft_version": expectations.draft_version,
        "account_id": expectations.account_id,
        "account_fingerprint": expectations.account_fingerprint,
        "from_address": expectations.from_address,
        "mime_artifact_id": str(materialized.get("mime_artifact_id") or ""),
        "mime_sha256": expectations.mime_sha256,
        "to": list(expectations.to),
        "cc": list(expectations.cc),
        "bcc": list(expectations.bcc),
        "subject": expectations.subject,
        "attachment_hashes": list(expectations.attachment_hashes),
        # Transport receipt detail.
        "local_action_id": receipt.local_action_id,
        "message_id": receipt.message_id,
        "status": receipt.status.value,
        "ledger_status": ledger_status.value,
        "accepted_recipients": list(receipt.accepted),
        "recipient_results": [
            {
                "recipient": item.recipient,
                "status": item.status.value,
                "error_code": item.error_code,
            }
            for item in receipt.recipient_results
        ],
        "server_code": receipt.server_code,
        "ledger_recorded": True,
    }


def _replay_view(
    expectations: _Expectations,
    materialized: Mapping[str, Any],
    state: MailDeliveryStatus,
) -> Mapping[str, Any]:
    if state is MailDeliveryStatus.UNKNOWN:
        raise OutcomeUnknownError(
            expectations.local_action_id, "the recorded transport outcome is unknown"
        )
    if state in {MailDeliveryStatus.FAILED, MailDeliveryStatus.PARTIAL}:
        raise DefinitiveToolFailure(
            "the local action id already reached a terminal transport state"
        )
    return {
        "draft_id": expectations.draft_id,
        "draft_version": expectations.draft_version,
        "account_id": expectations.account_id,
        "account_fingerprint": expectations.account_fingerprint,
        "from_address": expectations.from_address,
        "to": list(expectations.to),
        "cc": list(expectations.cc),
        "bcc": list(expectations.bcc),
        "subject": expectations.subject,
        "attachment_hashes": list(expectations.attachment_hashes),
        "local_action_id": expectations.local_action_id,
        "message_id": expectations.message_id,
        "mime_artifact_id": str(materialized.get("mime_artifact_id") or ""),
        "mime_sha256": expectations.mime_sha256,
        "status": state.value,
        "ledger_status": state.value,
        "accepted_recipients": [],
        "recipient_results": [],
        "server_code": None,
        "ledger_recorded": True,
        "idempotent_replay": True,
    }


def _text(value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value.strip() and not allow_empty):
        raise ValueError("expected a non-empty string")
    if len(value) > 512:
        raise ValueError("value is too long")
    if "\r" in value or "\n" in value:
        raise ValueError("value must not contain CR/LF")
    return value


def _addresses(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError("recipients must be a list of addresses")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        address = _text(item)
        if "@" not in address:
            raise ValueError("recipient is not an email address")
        lowered = address.lower()
        if lowered not in seen:
            seen.add(lowered)
            normalized.append(address)
    if len(normalized) > 100:
        raise ValueError("too many recipients")
    return tuple(normalized)


def _normalized(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple)):
        return set()
    return {str(item).strip().lower() for item in value if str(item).strip()}


def _digest(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("sha256 digest is required")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError("sha256 digest must be lowercase hexadecimal")
    return value


def _digests(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected a list of sha256 digests")
    if len(value) > 64:
        raise ValueError("too many attachments")
    return tuple(_digest(item) for item in value)


__all__ = [
    "MAIL_SEND_CAPABILITY",
    "ExtensionToolInvoker",
    "MailArtifactReader",
    "MailSendExecutor",
]
