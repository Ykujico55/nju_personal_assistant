"""Read-only ``host.mail.*`` capability exposed to trusted extension workers.

Only a host-registered ``account_id`` may cross this channel: the broker
resolves endpoints and credentials from the host-owned registry, so an
extension can never redirect a credential to another server.  No send operation
is exposed here; the only path to SMTP is the Tool Gateway executor.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from personal_assistant.core.extensions.artifact_access import ArtifactAccessError
from personal_assistant.core.extensions.errors import ExtensionOperationError
from personal_assistant.core.mail import (
    MailAccountNotFoundError,
    MailAccountRecord,
    MailAccountRegistry,
    MailDeliveryLedger,
    MailDeliveryStatus,
    MailError,
    MailReconciliationStatus,
    MailTransportBroker,
)

from .owners import MailExecutionOwnerRegistry

HOST_MAIL_ACCOUNT = "host.mail.account"
HOST_MAIL_DELIVERY_STATUS = "host.mail.delivery_status"
HOST_MAIL_PROBE = "host.mail.probe"
HOST_MAIL_FOLDERS = "host.mail.folders"
HOST_MAIL_FETCH = "host.mail.fetch"
HOST_MAIL_RECONCILE_SENT = "host.mail.reconcile_sent"
HOST_MAIL_METHODS = frozenset(
    {
        HOST_MAIL_ACCOUNT,
        HOST_MAIL_DELIVERY_STATUS,
        HOST_MAIL_PROBE,
        HOST_MAIL_FOLDERS,
        HOST_MAIL_FETCH,
        HOST_MAIL_RECONCILE_SENT,
    }
)

MAX_FETCH_LIMIT = 500


class MailCapabilityError(ExtensionOperationError):
    pass


@dataclass(frozen=True, slots=True)
class MailCapabilityContext:
    extension_id: str
    extension_version: str


class MailHostCapability:
    def __init__(
        self,
        broker: MailTransportBroker,
        accounts: MailAccountRegistry,
        *,
        ledger: MailDeliveryLedger | None = None,
        owners: MailExecutionOwnerRegistry | None = None,
    ) -> None:
        self._broker = broker
        self._accounts = accounts
        self._ledger = ledger
        self._owners = owners

    @property
    def available(self) -> bool:
        return self._broker.read_available

    async def _delivery_status(
        self, account_id: str, params: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ledger = self._ledger
        if ledger is None:
            raise MailCapabilityError(
                "MAIL_LEDGER_UNAVAILABLE", "the transport ledger is unavailable"
            )
        local_action_id = _text(params.get("local_action_id"))
        record = await ledger.get(local_action_id)
        if record is None:
            raise MailCapabilityError(
                "MAIL_ACTION_UNKNOWN", "the local action is not in the host ledger"
            )
        if record.account_id != account_id:
            raise MailCapabilityError(
                "MAIL_ACTION_BINDING_MISMATCH",
                "the local action belongs to a different account",
            )
        return {
            "local_action_id": record.local_action_id,
            "message_id": record.message_id,
            "status": record.status.value,
            "recipient_results": [
                {
                    "recipient": item.recipient,
                    "status": item.status.value,
                    "error_code": item.error_code,
                }
                for item in record.recipient_results
            ],
            "server_code": record.server_code,
            "diagnostic_code": record.diagnostic_code,
        }

    async def resolve_account(self, account_id: str) -> MailAccountRecord:
        try:
            return await self._accounts.resolve(account_id)
        except MailAccountNotFoundError as exc:
            raise MailCapabilityError(exc.code.value, str(exc)) from None

    async def handle(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        context: MailCapabilityContext,
    ) -> Any:
        del context
        if method not in HOST_MAIL_METHODS:
            raise MailCapabilityError("MAIL_PROTOCOL_ERROR", f"unknown mail method: {method}")
        account_id = _account_id(params)
        if method == HOST_MAIL_ACCOUNT:
            return _account_view(await self.resolve_account(account_id))
        if method == HOST_MAIL_DELIVERY_STATUS:
            return await self._delivery_status(account_id, params)
        account = await self.resolve_account(account_id)
        try:
            if method == HOST_MAIL_PROBE:
                session = await self._broker.read_session(account.account_id)
                try:
                    capabilities = await session.probe()
                finally:
                    await session.close()
                return {
                    "imap_capabilities": list(capabilities.imap_capabilities),
                    "auth_mechanisms": list(capabilities.auth_mechanisms),
                    "uidvalidity": capabilities.uidvalidity,
                    "exists": capabilities.exists,
                }
            if method == HOST_MAIL_FOLDERS:
                session = await self._broker.read_session(account.account_id)
                try:
                    folders = await session.list_folders()
                finally:
                    await session.close()
                return {
                    "folders": [
                        {
                            "name": item.name,
                            "delimiter": item.delimiter,
                            "attributes": list(item.attributes),
                            "selectable": item.selectable,
                        }
                        for item in folders
                    ]
                }
            if method == HOST_MAIL_FETCH:
                folder = _text(params.get("folder"))
                uidvalidity = _optional_int(params.get("uidvalidity"))
                start_uid = _int(params.get("start_uid", 0), minimum=0)
                limit = _int(params.get("limit", 100), minimum=1, maximum=MAX_FETCH_LIMIT)
                session = await self._broker.read_session(account.account_id)
                try:
                    fetch_result = await session.fetch(
                        folder,
                        uidvalidity=uidvalidity,
                        start_uid=start_uid,
                        limit=limit,
                    )
                finally:
                    await session.close()
                return {
                    "uidvalidity": fetch_result.uidvalidity,
                    "exists": fetch_result.exists,
                    "messages": [_message_view(item) for item in fetch_result.messages],
                }
            local_action_id = _text(params.get("local_action_id"))
            message_id = _text(params.get("message_id"))
            sent_folder = _text(params.get("sent_folder", "Sent"), allow_empty=True)
            ledger = self._ledger
            if ledger is None:
                return {
                    "status": MailReconciliationStatus.UNAVAILABLE.value,
                    "diagnostic_code": "MAIL_LEDGER_UNAVAILABLE",
                    "matches": [],
                }
            # Safe periodic sweep: lift crash-interrupted EXECUTING rows whose
            # lease expired and whose owner is not a live local incarnation.
            with contextlib.suppress(Exception):
                await ledger.recover_stale_executions(
                    active_owners=self._owners.active() if self._owners else ()
                )
            # Bind the request to the host ledger before touching the mailbox:
            # an extension may only reconcile the exact account and Message-ID
            # recorded by the host for that local action.
            record = await ledger.get(local_action_id)
            if record is None:
                raise MailCapabilityError(
                    "MAIL_ACTION_UNKNOWN", "the local action is not in the host ledger"
                )
            if record.account_id != account.account_id:
                raise MailCapabilityError(
                    "MAIL_ACTION_BINDING_MISMATCH",
                    "the local action belongs to a different account",
                )
            if record.message_id != message_id:
                raise MailCapabilityError(
                    "MAIL_ACTION_BINDING_MISMATCH",
                    "the local action is bound to a different Message-ID",
                )
            if record.status is not MailDeliveryStatus.UNKNOWN:
                # Already terminal (or still prepared/executing): never report a
                # mailbox match as a resolved outcome for a row it cannot lift.
                return {
                    "status": MailReconciliationStatus.UNAVAILABLE.value,
                    "diagnostic_code": "MAIL_ACTION_NOT_UNKNOWN",
                    "matches": [],
                }
            reconcile_result = await self._broker.reconcile_sent(
                account.account_id,
                local_action_id=local_action_id,
                message_id=message_id,
                sent_folder=sent_folder or "Sent",
            )
            if reconcile_result.status is MailReconciliationStatus.MATCHED:
                # Keep the authoritative host ledger in step with reconciliation;
                # the CAS must actually lift UNKNOWN -> SUCCEEDED before the
                # extension may treat the action as resolved.
                try:
                    lifted = await ledger.reconcile(
                        local_action_id,
                        account_id=account.account_id,
                        message_id=message_id,
                        status=MailDeliveryStatus.SUCCEEDED,
                        diagnostic_code="SENT_RECONCILED",
                    )
                except Exception:  # noqa: BLE001 - fail closed
                    lifted = None
                if lifted is not MailDeliveryStatus.SUCCEEDED:
                    return {
                        "status": MailReconciliationStatus.UNAVAILABLE.value,
                        "diagnostic_code": "MAIL_LEDGER_UNAVAILABLE",
                        "matches": [],
                    }
            return {
                "status": reconcile_result.status.value,
                "diagnostic_code": reconcile_result.diagnostic_code,
                "matches": [
                    {
                        "uid": item.uid,
                        "message_id": item.message_id,
                        "subject": item.subject,
                        "from_address": item.from_address,
                        "sent_at": item.sent_at,
                        "raw_sha256": hashlib.sha256(item.raw).hexdigest(),
                    }
                    for item in reconcile_result.matches
                    if reconcile_result.status
                    in {
                        MailReconciliationStatus.MATCHED,
                        MailReconciliationStatus.AMBIGUOUS,
                    }
                ],
            }
        except MailError as exc:
            raise MailCapabilityError(exc.code.value, str(exc)) from None
        except ArtifactAccessError as exc:
            raise MailCapabilityError("MAIL_PROTOCOL_ERROR", str(exc)) from None


def _account_view(record: MailAccountRecord) -> Mapping[str, Any]:
    """Non-secret account view for the extension; no endpoints, no handle."""

    return {
        "account_id": record.account_id,
        "address": record.address,
        "display_name": record.display_name,
        "read_enabled": record.read_enabled,
        "send_enabled": record.send_enabled,
        "fingerprint": record.fingerprint(),
    }


def _account_id(params: Mapping[str, Any]) -> str:
    value = params.get("account_id")
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise MailCapabilityError("MAIL_PROTOCOL_ERROR", "account_id is required")
    return value.strip()


def _message_view(item: Any) -> Mapping[str, Any]:
    import base64

    return {
        "uid": item.uid,
        "message_id": item.message_id,
        "subject": item.subject,
        "from_address": item.from_address,
        "to_addresses": list(item.to_addresses),
        "cc_addresses": list(item.cc_addresses),
        "sent_at": item.sent_at,
        "flags": list(item.flags),
        "size_bytes": item.size_bytes,
        "raw_base64": base64.b64encode(item.raw).decode("ascii"),
        "truncated": item.truncated,
    }


def _text(value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise MailCapabilityError("MAIL_PROTOCOL_ERROR", "expected a non-empty string")
    if len(value) > 1024 or "\r" in value or "\n" in value:
        raise MailCapabilityError("MAIL_PROTOCOL_ERROR", "string value is invalid")
    return value


def _int(value: Any, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MailCapabilityError("MAIL_PROTOCOL_ERROR", "expected an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise MailCapabilityError("MAIL_PROTOCOL_ERROR", "integer value is out of range")
    return int(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return _int(value, minimum=1)


__all__ = [
    "HOST_MAIL_ACCOUNT",
    "HOST_MAIL_DELIVERY_STATUS",
    "HOST_MAIL_FETCH",
    "HOST_MAIL_FOLDERS",
    "HOST_MAIL_METHODS",
    "HOST_MAIL_PROBE",
    "HOST_MAIL_RECONCILE_SENT",
    "MailCapabilityContext",
    "MailCapabilityError",
    "MailHostCapability",
]
