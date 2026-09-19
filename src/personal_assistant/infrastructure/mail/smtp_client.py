"""Real TLS SMTP single-message sender with partial-recipient accounting.

The sender never retries.  It distinguishes three failure classes:

* failure before the message data was submitted (connect, auth, MAIL, RCPT or
  ``DATA``-refused) is a definitive ``FAILED``;
* a connection loss while the message data was being written or while waiting
  for the final acceptance reply is ``UNKNOWN`` and must never be retried;
* an explicit non-2xx final reply is a definitive ``FAILED``.

The blocking SMTP session runs in a worker thread, but the socket is tracked so
a timeout or cancellation can shut it down and then wait for the thread to
finish before returning.  No background thread may keep executing DATA after
the caller has observed a timeout or cancellation.
"""

from __future__ import annotations

import asyncio
import contextlib
import smtplib
import socket
import ssl
import threading
from typing import Any

from personal_assistant.core.mail import (
    MailAccountBinding,
    MailDeliveryReceipt,
    MailDeliveryRequest,
    MailDeliveryStatus,
    MailError,
    MailErrorCode,
    MailPolicy,
    MailRecipientResult,
    MailRecipientStatus,
    MailSendLeaseGuard,
)

from .imap_client import DEFAULT_TIMEOUT_SECONDS, default_ssl_context

_RECIPIENT_ACCEPTED_CODES = frozenset({250, 251})
_SENDER_ACCEPTED_CODES = frozenset({250, 251})


class _SendAborted(Exception):
    """Internal: the caller aborted the dispatch (timeout/cancellation)."""


class _TrackedSmtp(smtplib.SMTP):
    """SMTP client whose socket is registered before DNS/TLS/commands run.

    Registering the raw socket *before* the TLS handshake means an abort during
    connect or handshake can still shut it down; every command path also checks
    the abort flag before proceeding.
    """

    def __init__(
        self,
        owner: TlsSmtpSender,
        host: str,
        port: int,
        timeout: float,
        *,
        context: ssl.SSLContext | None,
        family: int,
        sockaddr: tuple[object, ...],
    ) -> None:
        self._owner = owner
        self._tls_context = context
        self._family = family
        self._sockaddr = sockaddr
        super().__init__(host=host, port=port, timeout=timeout)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        # DNS is resolved by the async caller under its deadline; here we only
        # create and register the socket so an abort can always close it.
        raw = socket.socket(self._family, socket.SOCK_STREAM)
        self._owner._register_socket(raw)
        try:
            raw.settimeout(timeout)
            self._owner._check_abort()
            raw.connect(self._sockaddr)
            self._owner._check_abort()
            if self._tls_context is not None:
                wrapped = self._tls_context.wrap_socket(raw, server_hostname=host)
                self._owner._register_socket(wrapped)
                return wrapped
            return raw
        except BaseException:
            with contextlib.suppress(OSError):
                raw.close()
            raise


class TlsSmtpSender:
    def __init__(
        self,
        account: MailAccountBinding,
        password: str,
        *,
        policy: MailPolicy,
        ssl_context: ssl.SSLContext | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        guard: MailSendLeaseGuard | None = None,
    ) -> None:
        self._account = account
        self._password = password
        self._policy = policy
        self._ssl_context = ssl_context or default_ssl_context()
        self._timeout = timeout_seconds
        self._guard = guard
        self._lock = threading.Lock()
        self._active: smtplib.SMTP | None = None
        self._phase = "connect"
        self._running_threads = 0
        self._thread_done = threading.Event()
        self._abort = threading.Event()
        self._sockets: list[socket.socket] = []
        self._last_receipt: MailDeliveryReceipt | None = None

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    @property
    def running_threads(self) -> int:
        with self._lock:
            return self._running_threads

    async def send(self, request: MailDeliveryRequest) -> MailDeliveryReceipt:
        if not self._account.send_enabled:
            raise MailError(
                MailErrorCode.ACCOUNT_DISABLED, "sending is not enabled for this account"
            )
        if not self._policy.allow_send:
            raise MailError(MailErrorCode.SEND_DISABLED, "sending is disabled by host policy")
        recipients = request.recipients()
        if not recipients:
            raise MailError(MailErrorCode.RECIPIENT_NOT_ALLOWED, "no recipients supplied")
        denied = [
            address
            for address in recipients
            if not self._policy.recipient_allowed(address)
        ]
        if denied:
            raise MailError(
                MailErrorCode.RECIPIENT_NOT_ALLOWED,
                "one or more recipients are not on the controlled allowlist",
            )
        if len(request.mime_bytes) > self._policy.max_message_bytes:
            raise MailError(
                MailErrorCode.MESSAGE_TOO_LARGE, "message exceeds the configured size limit"
            )
        if not request.mime_bytes:
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "message bytes are required")
        self._thread_done.clear()
        self._abort.clear()
        self._last_receipt = None
        with self._lock:
            self._sockets.clear()
        loop = asyncio.get_running_loop()
        # Name resolution happens here, bounded by the same total deadline.  The
        # caller never proceeds to send on timeout/cancel, but note that
        # ``loop.getaddrinfo`` uses the default executor and a stuck OS resolver
        # thread cannot be forcibly killed by Python; it may outlive the call.
        # No socket or SMTP command is ever issued in that case.
        deadline = loop.time() + self._timeout
        try:
            addresses = await asyncio.wait_for(
                loop.getaddrinfo(
                    self._account.smtp_host,
                    self._account.smtp_port,
                    type=socket.SOCK_STREAM,
                ),
                timeout=self._timeout,
            )
        except TimeoutError:
            return self._abort_receipt(request, "name resolution timed out")
        except (socket.gaierror, OSError):
            return self._abort_receipt(request, "the SMTP host could not be resolved")
        if not addresses:
            return self._abort_receipt(request, "the SMTP host could not be resolved")
        family, _, _, _, sockaddr = addresses[0]
        remaining = deadline - loop.time()
        if remaining <= 0:
            return self._abort_receipt(request, "the SMTP session timed out")
        future = loop.run_in_executor(
            None, self._send_guarded, request, family, sockaddr
        )
        try:
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=remaining)
            except TimeoutError:
                self._abort_transport()
                await self._join_thread(future)
                return self._timeout_receipt(request)
            except asyncio.CancelledError:
                self._abort_transport()
                await self._join_thread(future)
                raise
        except MailError:
            raise
        except ssl.SSLCertVerificationError:
            raise MailError(
                MailErrorCode.TLS_FAILED, "the SMTP TLS certificate is not trusted"
            ) from None
        except (ssl.SSLError, OSError):
            raise MailError(MailErrorCode.UNAVAILABLE, "the SMTP server is unreachable") from None
        except smtplib.SMTPAuthenticationError:
            raise MailError(
                MailErrorCode.AUTH_FAILED, "the mail server rejected the credentials"
            ) from None
        except smtplib.SMTPNotSupportedError:
            raise MailError(
                MailErrorCode.AUTH_UNSUPPORTED, "the server does not support AUTH"
            ) from None
        except smtplib.SMTPException:
            return MailDeliveryReceipt(
                local_action_id=request.local_action_id,
                message_id=request.message_id,
                status=MailDeliveryStatus.FAILED,
                recipient_results=_failed_results(recipients),
                server_code=None,
            )

    def _timeout_receipt(self, request: MailDeliveryRequest) -> MailDeliveryReceipt:
        """If the worker actually completed before the deadline, its real receipt
        wins; never reclassify a completed 250 as FAILED."""

        receipt = self._last_receipt
        if receipt is not None:
            return receipt
        return self._abort_receipt(request, "the SMTP session timed out")

    def _abort_receipt(
        self, request: MailDeliveryRequest, message: str
    ) -> MailDeliveryReceipt:
        """Aborted before/while DATA: only an in-flight submission is unknown."""

        del message
        phase = self.phase
        recipients = request.recipients()
        if phase in {"data", "post_data", "done"}:
            # ``done`` without a stored receipt means the thread ended between
            # the final reply and recording; treat it as unresolved, not FAILED.
            return _receipt(
                request,
                MailDeliveryStatus.UNKNOWN,
                _unknown_results(recipients),
                server_code=None,
            )
        return _receipt(
            request,
            MailDeliveryStatus.FAILED,
            _failed_results(recipients),
            server_code=None,
        )

    async def _join_thread(self, future: asyncio.Future[Any]) -> None:
        """Wait for the worker thread to exit, surviving repeated cancellation."""

        loop = asyncio.get_running_loop()
        waiter = loop.run_in_executor(None, self._thread_done.wait, 10.0)
        try:
            while True:
                try:
                    await asyncio.shield(waiter)
                    break
                except asyncio.CancelledError:
                    if waiter.done():
                        break
                    continue
                except Exception:  # noqa: BLE001 - the thread result is handled elsewhere
                    break
        finally:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(waiter)
        await _join_future(future)

    def _set_phase(self, phase: str, connection: smtplib.SMTP | None) -> None:
        with self._lock:
            self._phase = phase
            self._active = connection

    def _abort_transport(self) -> None:
        self._abort.set()
        with self._lock:
            connection = self._active
            sockets = list(self._sockets)
        for registered in sockets:
            with contextlib.suppress(OSError):
                registered.shutdown(2)
            with contextlib.suppress(OSError):
                registered.close()
        if connection is None:
            return
        active_sock = connection.sock
        if active_sock is not None:
            with contextlib.suppress(OSError):
                active_sock.shutdown(2)
            with contextlib.suppress(OSError):
                active_sock.close()
        with contextlib.suppress(Exception):
            connection.close()

    def _register_socket(self, sock: socket.socket) -> None:
        with self._lock:
            self._sockets.append(sock)
        if self._abort.is_set():
            with contextlib.suppress(OSError):
                sock.close()

    def _check_abort(self) -> None:
        if self._abort.is_set():
            raise _SendAborted("the dispatch was aborted")
        guard = self._guard
        if guard is not None and not guard.valid:
            # The execution lease was lost or the account binding changed;
            # abort before the next command rather than racing a
            # reconciliation/resend elsewhere.
            self._abort.set()
            if self._guard_reason() == "ACCOUNT_CHANGED":
                raise MailError(
                    MailErrorCode.ACCOUNT_CHANGED,
                    "the account binding changed during the dispatch",
                )
            raise _SendAborted("the dispatch lease is no longer valid")

    def _guard_reason(self) -> str | None:
        guard = self._guard
        if guard is None:
            return None
        reason = getattr(guard, "reason", None)
        return reason if isinstance(reason, str) else None

    def _raise_if_account_changed(self) -> None:
        if self._guard_reason() == "ACCOUNT_CHANGED":
            raise MailError(
                MailErrorCode.ACCOUNT_CHANGED,
                "the account binding changed during the dispatch",
            )

    def _begin_critical_section(self) -> bool:
        guard = self._guard
        if guard is None:
            return True
        begin = getattr(guard, "begin_critical_section", None)
        if begin is None:
            return bool(guard.valid)
        return bool(begin())

    def _end_critical_section(self) -> None:
        guard = self._guard
        if guard is None:
            return
        end = getattr(guard, "end_critical_section", None)
        if end is not None:
            end()

    def _send_guarded(
        self,
        request: MailDeliveryRequest,
        family: int,
        sockaddr: tuple[object, ...],
    ) -> MailDeliveryReceipt:
        with self._lock:
            self._running_threads += 1
        try:
            receipt = self._send_sync(request, family=family, sockaddr=sockaddr)
            self._last_receipt = receipt
            return receipt
        finally:
            with self._lock:
                self._running_threads -= 1
                self._active = None
            self._thread_done.set()

    def _send_sync(
        self,
        request: MailDeliveryRequest,
        *,
        family: int,
        sockaddr: tuple[object, ...],
    ) -> MailDeliveryReceipt:
        phase = "connect"
        connection: smtplib.SMTP | None = None
        self._set_phase(phase, None)
        try:
            connection = self._connect(family=family, sockaddr=sockaddr)
            self._set_phase("pre_data", connection)
            phase = "pre_data"
            self._check_abort()
            self._login(connection)
            self._check_abort()
            code, _ = connection.mail(request.from_address)
            if code not in _SENDER_ACCEPTED_CODES:
                return _receipt(
                    request,
                    MailDeliveryStatus.FAILED,
                    _failed_results(request.recipients()),
                    server_code=str(code),
                )
            accepted: list[str] = []
            rejected: list[MailRecipientResult] = []
            for recipient in request.recipients():
                code, _ = connection.rcpt(recipient)
                if code in _RECIPIENT_ACCEPTED_CODES:
                    accepted.append(recipient)
                else:
                    rejected.append(
                        MailRecipientResult(
                            recipient=recipient,
                            status=MailRecipientStatus.REJECTED,
                            error_code=f"SMTP_{code}",
                        )
                    )
            if not accepted:
                return _receipt(
                    request,
                    MailDeliveryStatus.FAILED,
                    tuple(rejected),
                    server_code="ALL_RECIPIENTS_REJECTED",
                )
            phase = "data"
            self._set_phase(phase, connection)
            self._check_abort()
            if not self._begin_critical_section():
                self._raise_if_account_changed()
                return _receipt(
                    request,
                    MailDeliveryStatus.FAILED,
                    _failed_results(request.recipients()),
                    server_code=None,
                )
            try:
                code, _ = connection.docmd("DATA")
                if code != 354:
                    rejected.extend(
                        MailRecipientResult(
                            recipient=address,
                            status=MailRecipientStatus.REJECTED,
                            error_code=f"SMTP_{code}",
                        )
                        for address in accepted
                    )
                    return _receipt(
                        request,
                        MailDeliveryStatus.FAILED,
                        tuple(rejected),
                        server_code=str(code),
                    )
                # The account binding is checked and DATA is entered while the
                # cross-process lock is held: a concurrent registry change either
                # completed before this point (we already aborted) or waits until
                # this submission is committed.
                connection.send(_dot_stuffed(request.mime_bytes))
                phase = "post_data"
                self._set_phase(phase, connection)
                code, _ = connection.getreply()
            finally:
                self._end_critical_section()
            phase = "done"
            self._set_phase(phase, connection)
            if 200 <= code < 300:
                results = [
                    MailRecipientResult(
                        recipient=address, status=MailRecipientStatus.ACCEPTED
                    )
                    for address in accepted
                ] + rejected
                status = (
                    MailDeliveryStatus.PARTIAL
                    if rejected
                    else MailDeliveryStatus.SUCCEEDED
                )
                return _receipt(request, status, tuple(results), server_code=str(code))
            rejected.extend(
                MailRecipientResult(
                    recipient=address,
                    status=MailRecipientStatus.REJECTED,
                    error_code=f"SMTP_{code}",
                )
                for address in accepted
            )
            return _receipt(
                request,
                MailDeliveryStatus.FAILED,
                tuple(rejected),
                server_code=str(code),
            )
        except _SendAborted:
            if phase in {"data", "post_data", "done"}:
                return _receipt(
                    request,
                    MailDeliveryStatus.UNKNOWN,
                    _unknown_results(request.recipients()),
                    server_code=None,
                )
            return _receipt(
                request,
                MailDeliveryStatus.FAILED,
                _failed_results(request.recipients()),
                server_code=None,
            )
        except (smtplib.SMTPServerDisconnected, OSError, TimeoutError, ssl.SSLError):
            if phase in {"data", "post_data"}:
                return _receipt(
                    request,
                    MailDeliveryStatus.UNKNOWN,
                    _unknown_results(request.recipients()),
                    server_code=None,
                )
            return _receipt(
                request,
                MailDeliveryStatus.FAILED,
                _failed_results(request.recipients()),
                server_code=None,
            )
        finally:
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.quit()
                with contextlib.suppress(Exception):
                    connection.close()

    def _connect(
        self, *, family: int, sockaddr: tuple[object, ...]
    ) -> smtplib.SMTP:
        implicit = self._account.tls_mode == "implicit" or (
            self._account.tls_mode == "auto" and self._account.smtp_port == 465
        )
        starttls = self._account.tls_mode == "starttls" or (
            self._account.tls_mode == "auto" and self._account.smtp_port in {25, 587}
        )
        if implicit:
            return _TrackedSmtp(
                self,
                self._account.smtp_host,
                self._account.smtp_port,
                self._timeout,
                context=self._ssl_context,
                family=family,
                sockaddr=sockaddr,
            )
        if not starttls:
            raise MailError(
                MailErrorCode.PROTOCOL_ERROR,
                "the SMTP port does not imply a TLS mode; configure 465 or 587",
            )
        connection = _TrackedSmtp(
            self,
            self._account.smtp_host,
            self._account.smtp_port,
            self._timeout,
            context=None,
            family=family,
            sockaddr=sockaddr,
        )
        if connection.sock is not None:
            self._register_socket(connection.sock)
        code, _ = connection.ehlo()
        if code != 250:
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "SMTP EHLO failed")
        self._check_abort()
        code, _ = connection.starttls(context=self._ssl_context)
        if code != 220:
            raise MailError(MailErrorCode.TLS_FAILED, "SMTP STARTTLS failed")
        if connection.sock is not None:
            self._register_socket(connection.sock)
        connection.ehlo()
        return connection

    def _login(self, connection: smtplib.SMTP) -> None:
        try:
            connection.login(self._account.address, self._password)
        except smtplib.SMTPAuthenticationError:
            raise MailError(
                MailErrorCode.AUTH_FAILED, "the mail server rejected the credentials"
            ) from None
        except smtplib.SMTPNotSupportedError:
            raise MailError(
                MailErrorCode.AUTH_UNSUPPORTED, "the server does not support AUTH"
            ) from None
        except smtplib.SMTPException:
            raise MailError(
                MailErrorCode.AUTH_FAILED, "the mail server rejected the credentials"
            ) from None


async def _join_future(future: asyncio.Future[Any]) -> None:
    """Wait for the worker thread even under repeated cancellation."""

    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            if future.done():
                break
            continue
        except Exception:  # noqa: BLE001 - the thread result is handled by the receipt
            break
    with contextlib.suppress(Exception, asyncio.CancelledError):
        future.exception()


def _dot_stuffed(data: bytes) -> bytes:
    stuffed = data.replace(b"\r\n.", b"\r\n..")
    if stuffed.startswith(b"."):
        stuffed = b"." + stuffed
    if not stuffed.endswith(b"\r\n"):
        stuffed += b"\r\n"
    return stuffed + b".\r\n"


def _receipt(
    request: MailDeliveryRequest,
    status: MailDeliveryStatus,
    results: tuple[MailRecipientResult, ...],
    *,
    server_code: str | None,
) -> MailDeliveryReceipt:
    return MailDeliveryReceipt(
        local_action_id=request.local_action_id,
        message_id=request.message_id,
        status=status,
        recipient_results=results,
        server_code=server_code,
    )


def _unknown_results(recipients: tuple[str, ...]) -> tuple[MailRecipientResult, ...]:
    return tuple(
        MailRecipientResult(recipient=address, status=MailRecipientStatus.UNKNOWN)
        for address in recipients
    )


def _failed_results(recipients: tuple[str, ...]) -> tuple[MailRecipientResult, ...]:
    return tuple(
        MailRecipientResult(
            recipient=address,
            status=MailRecipientStatus.REJECTED,
            error_code="NOT_SUBMITTED",
        )
        for address in recipients
    )


__all__ = ["TlsSmtpSender"]
