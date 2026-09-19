"""Real TLS IMAP read-only client.

Certificates and hostnames are verified with the system trust store by default;
tests may inject a restricted SSLContext.  Only read commands are used:
``CAPABILITY``, ``LOGIN``/``AUTHENTICATE``, ``LIST``, ``SELECT ... (readonly)``,
``UID SEARCH`` and ``UID FETCH`` with ``BODY.PEEK``.  ``STORE``, ``EXPUNGE``,
``COPY``, ``MOVE``, ``APPEND`` and ``IDLE`` are never issued, so synchronization
cannot alter server-side read/move/delete state.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import re
import ssl
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from personal_assistant.core.mail import (
    MailAccountBinding,
    MailCapabilities,
    MailError,
    MailErrorCode,
    MailFetchedMessage,
    MailFetchResult,
    MailFolder,
    MailSendLeaseGuard,
)

from .mime import parse_message

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_MESSAGE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_MESSAGES = 200
MAX_FLAGS = 32

_LIST_RE = re.compile(
    r"^\((?P<attrs>[^)]*)\)\s+(?P<delim>NIL|\"[^\"]*\")\s+(?P<name>.+)$",
    re.IGNORECASE,
)
_FETCH_META_RE = re.compile(
    r"UID\s+(?P<uid>\d+).*?FLAGS\s+\((?P<flags>[^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)
_SIZE_RE = re.compile(r"RFC822\.SIZE\s+(?P<size>\d+)", re.IGNORECASE)
_DATE_RE = re.compile(r'INTERNALDATE\s+"(?P<date>[^"]+)"', re.IGNORECASE)

T = TypeVar("T")


def default_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


class TlsImapReadSession:
    """One connection; every call is bounded by a single total deadline."""

    def __init__(
        self,
        account: MailAccountBinding,
        password: str,
        *,
        ssl_context: ssl.SSLContext | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        guard: MailSendLeaseGuard | None = None,
        cleanup: Callable[[], None] | None = None,
    ) -> None:
        self._account = account
        self._password = password
        self._ssl_context = ssl_context or default_ssl_context()
        self._timeout = timeout_seconds
        self._max_message_bytes = max_message_bytes
        self._max_messages = max_messages
        self._guard = guard
        self._cleanup = cleanup
        self._connection: imaplib.IMAP4_SSL | None = None
        self._closed = False

    async def probe(self) -> MailCapabilities:
        return await self._run(self._probe_sync)

    async def list_folders(self) -> tuple[MailFolder, ...]:
        return await self._run(self._list_folders_sync)

    async def fetch(
        self,
        folder: str,
        *,
        uidvalidity: int | None,
        start_uid: int,
        limit: int,
    ) -> MailFetchResult:
        return await self._run(
            self._fetch_sync,
            folder,
            uidvalidity,
            max(0, start_uid),
            max(1, min(limit, self._max_messages)),
        )

    async def find_by_message_id(
        self, folder: str, message_id: str, *, limit: int = 10
    ) -> tuple[MailFetchedMessage, ...]:
        return await self._run(self._find_sync, folder, message_id, limit)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        connection = self._connection
        self._connection = None
        if self._cleanup is not None:
            with contextlib.suppress(Exception):
                self._cleanup()
        if connection is not None:
            try:
                connection.logout()
            except Exception:  # noqa: BLE001 - best-effort close
                self._shutdown_socket(connection)

    # -- thread-side implementation -----------------------------------------

    def _ensure_connection(self) -> imaplib.IMAP4_SSL:
        if self._closed:
            raise MailError(MailErrorCode.UNAVAILABLE, "the mail session is closed")
        self._check_guard()
        if self._connection is not None:
            return self._connection
        try:
            connection = imaplib.IMAP4_SSL(
                host=self._account.imap_host,
                port=self._account.imap_port,
                ssl_context=self._ssl_context,
                timeout=self._timeout,
            )
        except ssl.SSLCertVerificationError as exc:
            raise MailError(
                MailErrorCode.TLS_FAILED, "the IMAP TLS certificate is not trusted"
            ) from exc
        except (ssl.SSLError, OSError) as exc:
            raise MailError(
                MailErrorCode.UNAVAILABLE, "the IMAP server is unreachable"
            ) from exc
        self._connection = connection
        self._authenticate(connection)
        return connection

    def _authenticate(self, connection: imaplib.IMAP4_SSL) -> None:
        capabilities = self._capabilities(connection)
        auth_mechanisms = sorted(
            item[5:] for item in capabilities if item.startswith("AUTH=")
        )
        try:
            if "PLAIN" in auth_mechanisms:
                credentials = f"\0{self._account.address}\0{self._password}".encode()
                connection.authenticate("PLAIN", lambda _challenge: credentials)
                return
            if auth_mechanisms and "LOGINDISABLED" in capabilities:
                raise MailError(
                    MailErrorCode.AUTH_UNSUPPORTED,
                    "the server does not offer a supported authentication mechanism",
                )
            connection.login(self._account.address, self._password)
        except MailError:
            raise
        except imaplib.IMAP4.error as exc:
            raise MailError(
                MailErrorCode.AUTH_FAILED, "the mail server rejected the credentials"
            ) from exc
        except (ssl.SSLError, OSError) as exc:
            raise MailError(MailErrorCode.UNAVAILABLE, "the IMAP session failed") from exc

    def _capabilities(self, connection: imaplib.IMAP4_SSL) -> set[str]:
        try:
            typ, data = connection.capability()
        except imaplib.IMAP4.error as exc:
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "CAPABILITY failed") from exc
        except (ssl.SSLError, OSError) as exc:
            raise MailError(MailErrorCode.UNAVAILABLE, "the IMAP session failed") from exc
        if typ != "OK" or not data or not isinstance(data[0], bytes):
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "CAPABILITY returned no data")
        return {
            token.decode("ascii", "replace").upper()
            for token in data[0].split()
            if token
        }

    def _select_readonly(
        self, connection: imaplib.IMAP4_SSL, folder: str
    ) -> tuple[int, int]:
        quoted = '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'
        try:
            typ, data = connection.select(quoted, readonly=True)
        except imaplib.IMAP4.error as exc:
            raise MailError(
                MailErrorCode.FOLDER_UNAVAILABLE, "the mailbox could not be selected"
            ) from exc
        except (ssl.SSLError, OSError) as exc:
            raise MailError(MailErrorCode.UNAVAILABLE, "the IMAP session failed") from exc
        if typ != "OK":
            raise MailError(
                MailErrorCode.FOLDER_UNAVAILABLE, "the mailbox could not be selected"
            )
        exists = _safe_int(data[0]) if data else 0
        uidvalidity: int | None = None
        try:
            typ, validity = connection.response("UIDVALIDITY")
        except imaplib.IMAP4.error as exc:
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "UIDVALIDITY failed") from exc
        if typ == "UIDVALIDITY" and validity and validity[0]:
            try:
                uidvalidity = int(validity[0])
            except (TypeError, ValueError):
                uidvalidity = None
        if uidvalidity is None or uidvalidity <= 0:
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "the server returned no UIDVALIDITY")
        return exists, uidvalidity

    def _probe_sync(self) -> MailCapabilities:
        connection = self._ensure_connection()
        capabilities = self._capabilities(connection)
        auth_mechanisms = sorted(
            item[5:] for item in capabilities if item.startswith("AUTH=")
        )
        exists, uidvalidity = self._select_readonly(connection, "INBOX")
        return MailCapabilities(
            imap_capabilities=tuple(sorted(capabilities)),
            auth_mechanisms=tuple(auth_mechanisms),
            uidvalidity=uidvalidity,
            exists=exists,
        )

    def _list_folders_sync(self) -> tuple[MailFolder, ...]:
        connection = self._ensure_connection()
        try:
            typ, data = connection.list()
        except imaplib.IMAP4.error as exc:
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "LIST failed") from exc
        except (ssl.SSLError, OSError) as exc:
            raise MailError(MailErrorCode.UNAVAILABLE, "the IMAP session failed") from exc
        if typ != "OK":
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "LIST failed")
        folders: list[MailFolder] = []
        for item in data:
            if not isinstance(item, bytes):
                continue
            parsed = _LIST_RE.match(item.decode("utf-8", "replace"))
            if parsed is None:
                continue
            delimiter_raw = parsed.group("delim")
            delimiter = (
                None
                if delimiter_raw.upper() == "NIL"
                else delimiter_raw[1:-1].replace('\\\\', "\\")
            )
            name = _decode_mailbox_name(parsed.group("name"))
            if not name:
                continue
            attributes = parsed.group("attrs").split()
            selectable = "\\Noselect" not in attributes
            folders.append(
                MailFolder(
                    name=name,
                    delimiter=delimiter,
                    attributes=tuple(attributes),
                    selectable=selectable,
                )
            )
        return tuple(folders)

    def _metadata_sync(
        self, connection: imaplib.IMAP4_SSL, uid: str
    ) -> dict[str, Any] | None:
        try:
            typ, data = connection.uid(
                "FETCH", uid, "(UID FLAGS INTERNALDATE RFC822.SIZE)"
            )
        except imaplib.IMAP4.error as exc:
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "UID FETCH metadata failed") from exc
        if typ != "OK":
            return None
        return _parse_fetch_metadata(data)

    def _fetch_sync(
        self,
        folder: str,
        uidvalidity: int | None,
        start_uid: int,
        limit: int,
    ) -> MailFetchResult:
        connection = self._ensure_connection()
        exists, current_validity = self._select_readonly(connection, folder)
        if uidvalidity is not None and uidvalidity != current_validity:
            # The caller must rebuild its cursor; returning the new identity with
            # no messages is the safe, explicit signal.
            return MailFetchResult(uidvalidity=current_validity, exists=exists, messages=())
        try:
            typ, data = connection.uid("SEARCH", "", f"UID {start_uid}:*")
        except imaplib.IMAP4.error as exc:
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "UID SEARCH failed") from exc
        except (ssl.SSLError, OSError) as exc:
            raise MailError(MailErrorCode.UNAVAILABLE, "the IMAP session failed") from exc
        if typ != "OK" or not data or not isinstance(data[0], bytes):
            return MailFetchResult(uidvalidity=current_validity, exists=exists, messages=())
        uids = sorted(
            int(token)
            for token in data[0].split()
            if token.isdigit() and int(token) >= start_uid
        )[:limit]
        messages: list[MailFetchedMessage] = []
        for uid in uids:
            metadata = self._metadata_sync(connection, str(uid))
            if metadata is None:
                continue
            padded = f"{uid:010d}"
            if int(metadata.get("size", 0)) <= self._max_message_bytes:
                raw = self._fetch_body(connection, str(uid), "BODY.PEEK[]", full=True)
                truncated = raw is None
                payload = raw or b""
            else:
                raw = self._fetch_body(connection, str(uid), "BODY.PEEK[HEADER]", full=False)
                truncated = True
                payload = raw or b""
            del padded
            messages.append(
                _build_message(uid, metadata, payload, truncated=truncated)
            )
        return MailFetchResult(
            uidvalidity=current_validity, exists=exists, messages=tuple(messages)
        )

    def _find_sync(
        self, folder: str, message_id: str, limit: int
    ) -> tuple[MailFetchedMessage, ...]:
        connection = self._ensure_connection()
        try:
            self._select_readonly(connection, folder)
        except MailError as exc:
            if exc.code is MailErrorCode.FOLDER_UNAVAILABLE:
                return ()
            raise
        if "\r" in message_id or "\n" in message_id or '"' in message_id:
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "invalid message id for search")
        try:
            typ, data = connection.uid(
                "SEARCH", "", "HEADER", "Message-ID", f'"{message_id}"'
            )
        except imaplib.IMAP4.error as exc:
            raise MailError(
                MailErrorCode.PROTOCOL_ERROR, "Message-ID search failed"
            ) from exc
        except (ssl.SSLError, OSError) as exc:
            raise MailError(MailErrorCode.UNAVAILABLE, "the IMAP session failed") from exc
        if typ != "OK" or not data or not isinstance(data[0], bytes):
            return ()
        uids = sorted(int(token) for token in data[0].split() if token.isdigit())
        messages: list[MailFetchedMessage] = []
        for uid in uids[: max(1, limit)]:
            metadata = self._metadata_sync(connection, str(uid))
            if metadata is None:
                continue
            raw = self._fetch_body(connection, str(uid), "BODY.PEEK[HEADER]", full=False)
            messages.append(
                _build_message(uid, metadata, raw or b"", truncated=False)
            )
        return tuple(messages)

    def _fetch_body(
        self, connection: imaplib.IMAP4_SSL, uid: str, spec: str, *, full: bool
    ) -> bytes | None:
        try:
            typ, data = connection.uid("FETCH", uid, f"(UID {spec})")
        except imaplib.IMAP4.error as exc:
            raise MailError(MailErrorCode.PROTOCOL_ERROR, "UID FETCH body failed") from exc
        except (ssl.SSLError, OSError) as exc:
            raise MailError(MailErrorCode.UNAVAILABLE, "the IMAP session failed") from exc
        if typ != "OK":
            return None
        payload = _extract_literal(data)
        if payload is not None and len(payload) > self._max_message_bytes:
            payload = payload[: self._max_message_bytes]
            return payload if full else payload
        return payload

    # -- deadline and error mapping -----------------------------------------

    async def _run(self, func: Callable[..., T], *args: Any) -> T:
        if self._closed:
            raise MailError(MailErrorCode.UNAVAILABLE, "the mail session is closed")
        self._check_guard()
        try:
            async with asyncio.timeout(self._timeout):
                result = await asyncio.to_thread(func, *args)
            self._check_guard()
            return result
        except TimeoutError:
            self._abort()
            raise MailError(MailErrorCode.TIMEOUT, "the mail operation timed out") from None
        except asyncio.CancelledError:
            self._abort()
            raise
        except MailError:
            raise
        except ssl.SSLCertVerificationError:
            self._abort()
            raise MailError(
                MailErrorCode.TLS_FAILED, "the IMAP TLS certificate is not trusted"
            ) from None
        except ssl.SSLError:
            self._abort()
            raise MailError(MailErrorCode.TLS_FAILED, "the IMAP TLS handshake failed") from None
        except imaplib.IMAP4.error as exc:
            self._abort()
            raise _classify_imap_error(exc) from None
        except (OSError, ValueError):
            self._abort()
            raise MailError(MailErrorCode.UNAVAILABLE, "the IMAP session failed") from None

    def _abort(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            self._shutdown_socket(connection)

    def _check_guard(self) -> None:
        guard = self._guard
        if guard is not None and not guard.valid:
            raise MailError(
                MailErrorCode.ACCOUNT_CHANGED,
                "the account binding changed after this session was authorized",
            )

    @staticmethod
    def _shutdown_socket(connection: imaplib.IMAP4_SSL) -> None:
        try:
            sock = connection.socket()
        except Exception:  # noqa: BLE001 - the connection may never have opened
            return
        with contextlib.suppress(OSError):
            sock.shutdown(2)
        with contextlib.suppress(OSError):
            sock.close()


def _classify_imap_error(exc: imaplib.IMAP4.error) -> MailError:
    message = str(exc).lower()
    if exc.__class__.__name__ == "abort":
        return MailError(MailErrorCode.UNAVAILABLE, "the IMAP connection was lost")
    if any(token in message for token in ("rate", "throttl", "too many", "try again", "limit")):
        return MailError(MailErrorCode.RATE_LIMITED, "the mail server is rate limiting")
    if any(
        token in message
        for token in ("auth", "login", "credential", "password", "invalid user")
    ):
        return MailError(MailErrorCode.AUTH_FAILED, "the mail server rejected the credentials")
    return MailError(MailErrorCode.PROTOCOL_ERROR, "the IMAP command was rejected")


def _decode_mailbox_name(value: str) -> str:
    text = value.strip()
    if text in {"", "NIL"}:
        return ""
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return text


def _parse_fetch_metadata(data: list[Any]) -> dict[str, Any] | None:
    for item in data:
        raw = item[0] if isinstance(item, tuple) and item else item
        if not isinstance(raw, bytes):
            continue
        text = raw.decode("latin-1", "replace")
        uid_match = re.search(r"\bUID\s+(\d+)", text, re.IGNORECASE)
        if uid_match is None:
            continue
        metadata: dict[str, Any] = {"uid": int(uid_match.group(1))}
        flags_match = re.search(r"\bFLAGS\s+\(([^)]*)\)", text, re.IGNORECASE)
        flags = flags_match.group(1).split() if flags_match else []
        metadata["flags"] = flags[:MAX_FLAGS]
        size_match = _SIZE_RE.search(text)
        metadata["size"] = int(size_match.group("size")) if size_match else 0
        date_match = _DATE_RE.search(text)
        metadata["sent_at"] = date_match.group("date") if date_match else None
        return metadata
    return None


def _extract_literal(data: list[Any]) -> bytes | None:
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _safe_int(value: Any) -> int:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("ascii", "ignore")
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _internal_date_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = imaplib.Internaldate2tuple(f'INTERNALDATE "{value}"'.encode("ascii"))
    except Exception:  # noqa: BLE001 - malformed server date is data
        return None
    if parsed is None:
        return None
    try:
        moment = datetime(
            parsed[0],
            parsed[1],
            parsed[2],
            parsed[3],
            parsed[4],
            parsed[5],
            tzinfo=UTC,
        )
    except (TypeError, ValueError):
        return None
    return moment.isoformat()


def _build_message(
    uid: int, metadata: dict[str, Any], raw: bytes, *, truncated: bool
) -> MailFetchedMessage:
    parsed = parse_message(raw)
    sent_at = parsed.sent_at or _internal_date_to_iso(metadata.get("sent_at"))
    return MailFetchedMessage(
        uid=uid,
        message_id=parsed.message_id,
        subject=parsed.subject,
        from_address=parsed.from_address,
        to_addresses=parsed.to_addresses,
        cc_addresses=parsed.cc_addresses,
        sent_at=sent_at,
        flags=tuple(str(flag) for flag in metadata.get("flags", [])),
        size_bytes=int(metadata.get("size", len(raw))),
        raw=raw,
        truncated=truncated,
    )


async def probe_account(
    account: MailAccountBinding,
    password: str,
    *,
    ssl_context: ssl.SSLContext | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> MailCapabilities:
    session = TlsImapReadSession(
        account, password, ssl_context=ssl_context, timeout_seconds=timeout_seconds
    )
    try:
        return await session.probe()
    finally:
        await session.close()


__all__ = [
    "DEFAULT_MAX_MESSAGE_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "TlsImapReadSession",
    "default_ssl_context",
    "probe_account",
]
