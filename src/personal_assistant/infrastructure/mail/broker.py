"""Configured mail transport broker over the host credential store.

The broker only accepts a host-registered ``account_id``.  It resolves the
record, resolves the credential, and re-verifies the account generation before
any socket is created.  Every dispatch is then protected by a live,
fail-closed ``MailAccountDispatchGuard`` from the registry:

* ``valid`` re-reads the registry on every check (no polling cache) and fails
  closed on read errors;
* ``begin_critical_section`` takes the same cross-process exclusive lock that
  account writers take, re-reads the binding, and holds the lock until the DATA
  submission has been acknowledged.  A registry change therefore either
  completes before DATA (send aborts with ``ACCOUNT_CHANGED``) or waits until
  the already-started submission is committed.
"""

from __future__ import annotations

import ssl
from typing import Protocol

from personal_assistant.core.mail import (
    MailAccountBinding,
    MailAccountRecord,
    MailAccountRegistry,
    MailDeliveryReceipt,
    MailDeliveryRequest,
    MailError,
    MailErrorCode,
    MailPolicy,
    MailReconciliationResult,
    MailReconciliationStatus,
    MailSendLeaseGuard,
    MailTransportBroker,
)
from personal_assistant.core.secrets import SecretHandle, SecretStorePort

from .imap_client import (
    DEFAULT_MAX_MESSAGE_BYTES,
    DEFAULT_MAX_MESSAGES,
    DEFAULT_TIMEOUT_SECONDS,
    TlsImapReadSession,
    default_ssl_context,
)
from .owners import CompositeMailGuard
from .smtp_client import TlsSmtpSender

MAIL_CREDENTIAL_KIND = "mail_client_password"


class SmtpSenderPort(Protocol):
    """One SMTP submission attempt; implemented by ``TlsSmtpSender``."""

    async def send(self, request: MailDeliveryRequest) -> MailDeliveryReceipt: ...


class SmtpSenderFactory(Protocol):
    """Injectable constructor used by tests to wrap the real sender."""

    def __call__(
        self,
        account: MailAccountBinding,
        password: str,
        *,
        policy: MailPolicy,
        ssl_context: ssl.SSLContext,
        timeout_seconds: float,
        guard: MailSendLeaseGuard | None = None,
    ) -> SmtpSenderPort: ...


def binding_for(record: MailAccountRecord) -> MailAccountBinding:
    return MailAccountBinding(
        account_id=record.account_id,
        address=record.address,
        imap_host=record.imap_host,
        imap_port=record.imap_port,
        smtp_host=record.smtp_host,
        smtp_port=record.smtp_port,
        secret_handle=SecretHandle(id=record.secret_handle_id, kind=MAIL_CREDENTIAL_KIND),
        display_name=record.display_name,
        read_enabled=record.read_enabled,
        send_enabled=record.send_enabled,
        tls_mode=record.tls_mode,
    )


class ConfiguredMailTransportBroker(MailTransportBroker):
    """The only host component that opens IMAP or SMTP connections."""

    def __init__(
        self,
        secret_store: SecretStorePort,
        accounts: MailAccountRegistry,
        *,
        policy: MailPolicy | None = None,
        ssl_context: ssl.SSLContext | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        max_fetch_messages: int = DEFAULT_MAX_MESSAGES,
        smtp_factory: SmtpSenderFactory | None = None,
    ) -> None:
        self._secret_store = secret_store
        self._accounts = accounts
        self._policy = policy or MailPolicy()
        self._ssl_context = ssl_context or default_ssl_context()
        self._timeout = timeout_seconds
        self._max_message_bytes = max_message_bytes
        self._max_fetch_messages = max_fetch_messages
        self._smtp_factory: SmtpSenderFactory = smtp_factory or TlsSmtpSender

    @property
    def read_available(self) -> bool:
        return True

    @property
    def send_available(self) -> bool:
        return self._policy.allow_send

    @property
    def policy(self) -> MailPolicy:
        return self._policy

    async def read_session(self, account_id: str) -> TlsImapReadSession:
        lease = await self._accounts.resolve(account_id)
        if not lease.read_enabled:
            raise MailError(
                MailErrorCode.ACCOUNT_DISABLED, "reading is not enabled for this account"
            )
        password = await self._resolve(lease)
        record = await self._verify_active(lease)
        guard = self._accounts.dispatch_guard(record)
        return TlsImapReadSession(
            binding_for(record),
            password,
            ssl_context=self._ssl_context,
            timeout_seconds=self._timeout,
            max_message_bytes=self._max_message_bytes,
            max_messages=self._max_fetch_messages,
            guard=guard,
        )

    async def send(
        self,
        account_id: str,
        request: MailDeliveryRequest,
        *,
        guard: MailSendLeaseGuard | None = None,
    ) -> MailDeliveryReceipt:
        lease = await self._accounts.resolve(account_id)
        password = await self._resolve(lease)
        record = await self._verify_active(lease)
        account_guard = self._accounts.dispatch_guard(record)
        combined = CompositeMailGuard(guard, account_guard)
        sender = self._smtp_factory(
            binding_for(record),
            password,
            policy=self._policy,
            ssl_context=self._ssl_context,
            timeout_seconds=self._timeout,
            guard=combined,
        )
        return await sender.send(request)

    async def reconcile_sent(
        self,
        account_id: str,
        *,
        local_action_id: str,
        message_id: str,
        sent_folder: str = "Sent",
    ) -> MailReconciliationResult:
        try:
            session = await self.read_session(account_id)
        except MailError as exc:
            return MailReconciliationResult(
                local_action_id=local_action_id,
                message_id=message_id,
                status=MailReconciliationStatus.UNAVAILABLE,
                diagnostic_code=exc.code.value,
            )
        try:
            matches = await session.find_by_message_id(sent_folder, message_id)
        except MailError as exc:
            return MailReconciliationResult(
                local_action_id=local_action_id,
                message_id=message_id,
                status=MailReconciliationStatus.UNAVAILABLE,
                diagnostic_code=exc.code.value,
            )
        finally:
            await session.close()
        if not matches:
            return MailReconciliationResult(
                local_action_id=local_action_id,
                message_id=message_id,
                status=MailReconciliationStatus.NOT_FOUND,
            )
        if len(matches) > 1:
            return MailReconciliationResult(
                local_action_id=local_action_id,
                message_id=message_id,
                status=MailReconciliationStatus.AMBIGUOUS,
                matches=matches,
                diagnostic_code="MULTIPLE_MATCHES",
            )
        return MailReconciliationResult(
            local_action_id=local_action_id,
            message_id=message_id,
            status=MailReconciliationStatus.MATCHED,
            matches=matches,
        )

    async def aclose(self) -> None:
        return None

    async def _verify_active(self, lease: MailAccountRecord) -> MailAccountRecord:
        try:
            current = await self._accounts.resolve(lease.account_id)
        except Exception:  # noqa: BLE001 - missing account fails closed
            raise MailError(
                MailErrorCode.ACCOUNT_CHANGED,
                "the account was removed while the operation was starting",
            ) from None
        if (
            current.generation != lease.generation
            or current.fingerprint() != lease.fingerprint()
        ):
            raise MailError(
                MailErrorCode.ACCOUNT_CHANGED,
                "the account changed while the operation was starting",
            )
        return current

    async def _resolve(self, record: MailAccountRecord) -> str:
        handle = SecretHandle(id=record.secret_handle_id, kind=MAIL_CREDENTIAL_KIND)
        if not handle.id:
            raise MailError(
                MailErrorCode.CREDENTIAL_UNAVAILABLE, "the account credential handle is invalid"
            )
        try:
            password = await self._secret_store.resolve_for_broker(
                handle, purpose="mail.transport"
            )
        except Exception:  # noqa: BLE001 - the credential backend must fail closed
            raise MailError(
                MailErrorCode.CREDENTIAL_UNAVAILABLE,
                "the host credential backend could not resolve this account",
            ) from None
        if not isinstance(password, str) or not password:
            raise MailError(
                MailErrorCode.CREDENTIAL_UNAVAILABLE,
                "the host credential backend returned no credential",
            )
        return password


__all__ = ["ConfiguredMailTransportBroker", "binding_for", "MAIL_CREDENTIAL_KIND"]
