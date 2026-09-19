"""Provider-agnostic mail contracts.

These ports keep credentials and network connections inside the host.  An
extension describes an account and a delivery request; the host
``MailTransportBroker`` resolves the ``SecretHandle`` through the host credential
store, verifies TLS certificates and performs the read-only IMAP or SMTP work.
No type here contains a vendor, server or business-extension branch.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.core.secrets import SecretHandle


class MailErrorCode(StrEnum):
    TLS_FAILED = "MAIL_TLS_FAILED"
    AUTH_FAILED = "MAIL_AUTH_FAILED"
    AUTH_UNSUPPORTED = "MAIL_AUTH_UNSUPPORTED"
    UNAVAILABLE = "MAIL_UNAVAILABLE"
    TIMEOUT = "MAIL_TIMEOUT"
    RATE_LIMITED = "MAIL_RATE_LIMITED"
    PROTOCOL_ERROR = "MAIL_PROTOCOL_ERROR"
    ACCOUNT_DISABLED = "MAIL_ACCOUNT_DISABLED"
    ACCOUNT_UNKNOWN = "MAIL_ACCOUNT_UNKNOWN"
    ACCOUNT_CHANGED = "MAIL_ACCOUNT_CHANGED"
    SEND_DISABLED = "MAIL_SEND_DISABLED"
    RECIPIENT_NOT_ALLOWED = "MAIL_RECIPIENT_NOT_ALLOWED"
    MESSAGE_TOO_LARGE = "MAIL_MESSAGE_TOO_LARGE"
    FOLDER_UNAVAILABLE = "MAIL_FOLDER_UNAVAILABLE"
    CREDENTIAL_UNAVAILABLE = "MAIL_CREDENTIAL_UNAVAILABLE"


_NEEDS_USER_ACTION = frozenset(
    {
        MailErrorCode.AUTH_FAILED,
        MailErrorCode.AUTH_UNSUPPORTED,
        MailErrorCode.CREDENTIAL_UNAVAILABLE,
    }
)


class MailError(RuntimeError):
    """Typed broker failure; never carries credentials or raw server text."""

    def __init__(self, code: MailErrorCode, message: str = "") -> None:
        super().__init__(message or code.value)
        self.code = code

    @property
    def needs_user_action(self) -> bool:
        return self.code in _NEEDS_USER_ACTION


class MailRecipientStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class MailDeliveryStatus(StrEnum):
    #: Durable dispatch state machine: PREPARED -> EXECUTING -> terminal.
    #: A terminal state never moves back (reconciliation may only lift UNKNOWN).
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"

    @property
    def terminal(self) -> bool:
        return self in {
            MailDeliveryStatus.SUCCEEDED,
            MailDeliveryStatus.PARTIAL,
            MailDeliveryStatus.FAILED,
            MailDeliveryStatus.UNKNOWN,
        }


class MailLedgerConflictError(RuntimeError):
    code = "MAIL_LEDGER_CONFLICT"


class MailLedgerStateError(RuntimeError):
    code = "MAIL_LEDGER_STATE"


class MailReconciliationStatus(StrEnum):
    MATCHED = "MATCHED"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class MailAccountBinding:
    """Non-secret account metadata plus an opaque credential handle."""

    account_id: str
    address: str
    imap_host: str
    smtp_host: str
    secret_handle: SecretHandle
    imap_port: int = 993
    smtp_port: int = 465
    display_name: str = ""
    read_enabled: bool = True
    send_enabled: bool = False
    #: ``auto`` uses implicit TLS on 465 and STARTTLS on 25/587; tests may pin
    #: one mode explicitly.  TLS verification always stays enabled.
    tls_mode: str = "auto"

    def __post_init__(self) -> None:
        for name, value in (
            ("account_id", self.account_id),
            ("address", self.address),
            ("imap_host", self.imap_host),
            ("smtp_host", self.smtp_host),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if "@" not in self.address:
            raise ValueError("address must be an email address")
        if not 1 <= self.imap_port <= 65535 or not 1 <= self.smtp_port <= 65535:
            raise ValueError("mail ports must be valid TCP ports")
        if self.tls_mode not in {"auto", "implicit", "starttls"}:
            raise ValueError("tls_mode must be auto, implicit or starttls")
        if isinstance(self.secret_handle, str) or not self.secret_handle.id:
            raise ValueError("secret_handle must be a SecretHandle")
        if self.send_enabled and not self.read_enabled:
            # Sending requires resolving the same credential; keep the two
            # capabilities independent but never allow send without read setup.
            raise ValueError("send_enabled requires read_enabled")


@dataclass(frozen=True, slots=True)
class MailCapabilities:
    imap_capabilities: tuple[str, ...]
    auth_mechanisms: tuple[str, ...]
    uidvalidity: int
    exists: int
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class MailFolder:
    name: str
    delimiter: str | None
    attributes: tuple[str, ...]
    selectable: bool = True


@dataclass(frozen=True, slots=True)
class MailFetchedMessage:
    uid: int
    message_id: str | None
    subject: str | None
    from_address: str | None
    to_addresses: tuple[str, ...]
    cc_addresses: tuple[str, ...]
    sent_at: str | None
    flags: tuple[str, ...]
    size_bytes: int
    raw: bytes
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class MailFetchResult:
    uidvalidity: int
    exists: int
    messages: tuple[MailFetchedMessage, ...]


@dataclass(frozen=True, slots=True)
class MailRecipientResult:
    recipient: str
    status: MailRecipientStatus
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class MailDeliveryRequest:
    """Exact pre-built bytes plus the envelope summary they must match."""

    local_action_id: str
    message_id: str
    from_address: str
    to: tuple[str, ...]
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    subject: str = ""
    mime_bytes: bytes = b""

    def recipients(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for address in (*self.to, *self.cc, *self.bcc):
            seen.setdefault(address, None)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class MailEnvelope:
    """Canonical envelope summary bound by an approval snapshot."""

    from_address: str
    to: tuple[str, ...]
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    subject: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    references: tuple[str, ...] = ()
    attachment_hashes: tuple[str, ...] = ()
    body_sha256: str = ""
    mime_sha256: str = ""


@runtime_checkable
class MailSendLeaseGuard(Protocol):
    """Thread-visible validity check for an in-flight dispatch.

    A guard may also arbitrate a *critical section*: the transport acquires it
    immediately before submitting message data and releases it after the final
    server reply.  Account writers take the matching exclusive lock, so a
    registry change either completes before the section (send aborts) or waits
    until the already-started DATA submission is committed.
    """

    @property
    def valid(self) -> bool: ...

    @property
    def reason(self) -> str | None:
        """Stable diagnostic reason when invalid (for example ACCOUNT_CHANGED)."""

    def begin_critical_section(self, timeout_seconds: float = 5.0) -> bool: ...

    def end_critical_section(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MailDeliveryReceipt:
    local_action_id: str
    message_id: str
    status: MailDeliveryStatus
    recipient_results: tuple[MailRecipientResult, ...] = ()
    server_code: str | None = None

    @property
    def accepted(self) -> tuple[str, ...]:
        return tuple(
            item.recipient
            for item in self.recipient_results
            if item.status is MailRecipientStatus.ACCEPTED
        )

    @property
    def rejected(self) -> tuple[MailRecipientResult, ...]:
        return tuple(
            item
            for item in self.recipient_results
            if item.status is MailRecipientStatus.REJECTED
        )


@dataclass(frozen=True, slots=True)
class MailReconciliationResult:
    local_action_id: str
    message_id: str
    status: MailReconciliationStatus
    matches: tuple[MailFetchedMessage, ...] = ()
    diagnostic_code: str | None = None


@dataclass(frozen=True, slots=True)
class MailDeliveryRecord:
    """Durable transport ledger entry; never contains body bytes or credentials."""

    local_action_id: str
    account_id: str
    message_id: str
    status: MailDeliveryStatus
    envelope_digest: str
    mime_sha256: str
    recipient_results: tuple[MailRecipientResult, ...] = ()
    server_code: str | None = None
    diagnostic_code: str | None = None
    owner_id: str | None = None
    lease_expires_at: datetime | None = None


class MailDeliveryLedger(Protocol):
    """Host-side effectively-once transport ledger keyed by local action id.

    The state machine is ``PREPARED -> EXECUTING -> terminal``.  The dispatch
    intent must be durable before any SMTP connection is opened, the completion
    CAS must succeed before a success is reported, and terminal states never
    move back.  Only a persisted ``UNKNOWN`` may be lifted by reconciliation.
    """

    async def prepare(self, record: MailDeliveryRecord) -> MailDeliveryStatus:
        """Atomically insert PREPARED or return the existing state.

        A different envelope digest for the same local action id is a
        ``MailLedgerConflictError``.
        """

    async def begin_execution(
        self,
        local_action_id: str,
        envelope_digest: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> MailDeliveryStatus:
        """CAS PREPARED -> EXECUTING and claim a bounded lease.

        A second concurrent caller that finds a live EXECUTING lease receives
        EXECUTING unchanged; it must never burn the first caller's in-flight
        execution.  Returns the state that was actually found.
        """

    async def finalize(
        self,
        local_action_id: str,
        envelope_digest: str,
        *,
        status: MailDeliveryStatus,
        recipient_results: tuple[MailRecipientResult, ...] = (),
        server_code: str | None = None,
        diagnostic_code: str | None = None,
    ) -> MailDeliveryStatus:
        """CAS EXECUTING -> terminal; idempotent for an already terminal row."""

    async def reconcile(
        self,
        local_action_id: str,
        *,
        account_id: str,
        message_id: str,
        status: MailDeliveryStatus,
        diagnostic_code: str | None = None,
    ) -> MailDeliveryStatus:
        """Lift a persisted UNKNOWN to a reconciled terminal state.

        The account and Message-ID bindings are part of the CAS guard: a
        reconciliation for another account or message can never lift a row.
        """

    async def heartbeat(
        self, local_action_id: str, owner_id: str, *, lease_seconds: float
    ) -> bool:
        """Renew the execution lease; False means the owner lost the row."""

    async def recover_stale_executions(
        self, *, active_owners: Collection[str] = ()
    ) -> tuple[str, ...]:
        """Atomically turn lease-expired EXECUTING rows into UNKNOWN.

        Rows owned by a live local ``active_owners`` incarnation are never
        touched.  Called on host startup and before read-only reconciliation so
        an interrupted dispatch becomes reconcilable instead of stuck in
        EXECUTING forever, while a slow-but-alive sender is protected by its
        heartbeat.
        """

    async def get(self, local_action_id: str) -> MailDeliveryRecord | None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MailAccountRecord:
    account_id: str
    address: str
    imap_host: str
    smtp_host: str
    secret_handle_id: str
    imap_port: int = 993
    smtp_port: int = 465
    display_name: str = ""
    read_enabled: bool = True
    send_enabled: bool = False
    tls_mode: str = "auto"
    #: Monotonic per-account revision owned by the registry; any admin edit
    #: (upsert/replace) bumps it so older leases and approvals become invalid.
    generation: int = 0

    def __post_init__(self) -> None:
        for name, value in (
            ("account_id", self.account_id),
            ("address", self.address),
            ("imap_host", self.imap_host),
            ("smtp_host", self.smtp_host),
            ("secret_handle_id", self.secret_handle_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if "@" not in self.address:
            raise ValueError("address must be an email address")
        if not 1 <= self.imap_port <= 65535 or not 1 <= self.smtp_port <= 65535:
            raise ValueError("mail ports must be valid TCP ports")
        if self.tls_mode not in {"auto", "implicit", "starttls"}:
            raise ValueError("tls_mode must be auto, implicit or starttls")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 0
        ):
            raise ValueError("generation must be a non-negative integer")
        if self.send_enabled and not self.read_enabled:
            raise ValueError("send_enabled requires read_enabled")

    def fingerprint(self) -> str:
        """Canonical digest bound by a send approval.

        Any endpoint, port, address, credential handle or enabled flag change
        changes the fingerprint and invalidates older approvals.
        """

        return canonical_sha256(
            {
                "account_id": self.account_id,
                "address": self.address,
                "imap_host": self.imap_host,
                "imap_port": self.imap_port,
                "smtp_host": self.smtp_host,
                "smtp_port": self.smtp_port,
                "secret_handle_id": self.secret_handle_id,
                "display_name": self.display_name,
                "read_enabled": self.read_enabled,
                "send_enabled": self.send_enabled,
                "tls_mode": self.tls_mode,
                "generation": self.generation,
            }
        )


class MailAccountNotFoundError(MailError):
    def __init__(self, account_id: str) -> None:
        super().__init__(
            MailErrorCode.ACCOUNT_UNKNOWN, f"mail account is not registered: {account_id}"
        )


class MailReadSession(Protocol):
    """One read-only IMAP session; all mutating commands are absent."""

    async def probe(self) -> MailCapabilities: ...

    async def list_folders(self) -> tuple[MailFolder, ...]: ...

    async def fetch(
        self,
        folder: str,
        *,
        uidvalidity: int | None,
        start_uid: int,
        limit: int,
    ) -> MailFetchResult: ...

    async def find_by_message_id(
        self, folder: str, message_id: str, *, limit: int = 10
    ) -> tuple[MailFetchedMessage, ...]: ...

    async def close(self) -> None: ...


class MailAccountRegistry(Protocol):
    """Host-side source of truth for non-secret account metadata."""

    async def resolve(self, account_id: str) -> MailAccountRecord:
        """Return the registered account (with its current generation).

        The returned record acts as a short-lived lease: callers that await
        before using it must call :meth:`verify` afterwards.
        """

    async def list(self) -> tuple[MailAccountRecord, ...]: ...

    async def upsert(self, record: MailAccountRecord) -> None: ...

    async def verify(self, lease: MailAccountRecord) -> bool:
        """True only when the lease's account still exists unchanged.

        Implementations compare the stored generation and fingerprint, so a
        rebind, disable or delete after the lease was taken fails closed.
        """

    async def replace_all(self, records: tuple[MailAccountRecord, ...]) -> None: ...

    async def delete(self, account_id: str) -> None: ...

    def dispatch_guard(self, lease: MailAccountRecord) -> MailSendLeaseGuard:
        """Return a live, fail-closed guard for one account dispatch.

        The guard performs live reads (never a polling cache), invalidates on
        any read failure, and provides the cross-process critical section the
        transport uses around DATA submission.
        """


class MailTransportBroker(Protocol):
    @property
    def read_available(self) -> bool: ...

    @property
    def send_available(self) -> bool: ...

    async def read_session(self, account_id: str) -> MailReadSession: ...

    async def send(
        self,
        account_id: str,
        request: MailDeliveryRequest,
        *,
        guard: MailSendLeaseGuard | None = None,
    ) -> MailDeliveryReceipt: ...

    async def reconcile_sent(
        self,
        account_id: str,
        *,
        local_action_id: str,
        message_id: str,
        sent_folder: str = "Sent",
    ) -> MailReconciliationResult: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MailPolicy:
    """Host-side send policy: separate from read access on purpose."""

    allow_send: bool = False
    allowed_recipients: frozenset[str] = field(default_factory=frozenset)
    max_message_bytes: int = 2 * 1024 * 1024
    max_fetch_messages: int = 200

    def recipient_allowed(self, address: str) -> bool:
        return address.strip().lower() in self.allowed_recipients


__all__ = [
    "MailAccountBinding",
    "MailAccountNotFoundError",
    "MailAccountRecord",
    "MailAccountRegistry",
    "MailCapabilities",
    "MailDeliveryReceipt",
    "MailDeliveryRecord",
    "MailDeliveryLedger",
    "MailDeliveryRequest",
    "MailDeliveryStatus",
    "MailEnvelope",
    "MailLedgerConflictError",
    "MailLedgerStateError",
    "MailError",
    "MailErrorCode",
    "MailFetchResult",
    "MailFetchedMessage",
    "MailFolder",
    "MailPolicy",
    "MailReadSession",
    "MailRecipientResult",
    "MailRecipientStatus",
    "MailSendLeaseGuard",
    "MailReconciliationResult",
    "MailReconciliationStatus",
    "MailTransportBroker",
]
