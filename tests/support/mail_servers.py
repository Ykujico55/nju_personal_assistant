"""F06 test support: simulated TLS IMAP/SMTP servers over real sockets.

These servers speak a deliberately small subset of the real protocols but use
real TLS, real sockets and the real client code paths.  They let the tests prove
read-only IMAP behavior, partial-recipient rejection and DATA-phase
disconnections without ever touching a real mailbox.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import re
import ssl
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@dataclass(frozen=True, slots=True)
class TlsPair:
    server_context: ssl.SSLContext
    client_context: ssl.SSLContext
    directory: Path


def generate_tls_pair(hostname: str = "localhost") -> TlsPair:
    directory = Path(tempfile.mkdtemp(prefix="pa_mail_tls_"))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, hostname)]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(hostname),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "server.pem"
    key_path = directory / "server.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(cert_path), str(key_path))
    client_context = ssl.create_default_context(cafile=str(cert_path))
    client_context.check_hostname = True
    client_context.verify_mode = ssl.CERT_REQUIRED
    return TlsPair(
        server_context=server_context,
        client_context=client_context,
        directory=directory,
    )


@dataclass(slots=True)
class ImapMessage:
    uid: int
    raw: bytes
    flags: tuple[str, ...] = ("\\Seen",)


@dataclass(slots=True)
class ImapState:
    username: str = "student@smail.nju.edu.cn"
    password: str = "client-password-1"
    capabilities: tuple[str, ...] = ("IMAP4rev1", "AUTH=PLAIN", "UIDPLUS")
    folders: dict[str, list[ImapMessage]] = field(default_factory=dict)
    uidvalidity: dict[str, int] = field(default_factory=dict)
    logins: int = 0
    commands: list[str] = field(default_factory=list)
    select_readonly: dict[str, bool] = field(default_factory=dict)
    mutated: list[str] = field(default_factory=list)
    stall: bool = False
    connections: int = 0


class SimulatedImapServer:
    """Minimal read-capable IMAP4rev1 server; records every command."""

    def __init__(self, state: ImapState, ssl_context: ssl.SSLContext) -> None:
        self.state = state
        self._ssl_context = ssl_context
        self._server: asyncio.AbstractServer | None = None
        self.port = 0
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, host="127.0.0.1", port=0, ssl=self._ssl_context
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await self._serve(reader, writer)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        selected: str | None = None
        self.state.connections += 1
        await self._write(writer, b"* OK [CAPABILITY IMAP4rev1] smail test server\r\n")
        if self.state.stall:
            await asyncio.sleep(3600)
            return
        while True:
            line = await reader.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            self.state.commands.append(text)
            parts = text.split(" ")
            tag = parts[0] if parts else ""
            command = parts[1].upper() if len(parts) > 1 else ""
            if command == "CAPABILITY":
                caps = " ".join(self.state.capabilities)
                await self._write(writer, f"* CAPABILITY {caps}\r\n".encode())
                await self._ok(writer, tag, "CAPABILITY completed")
            elif command == "LOGIN":
                if (
                    len(parts) >= 4
                    and parts[2].strip('"') == self.state.username
                    and parts[3].strip('"') == self.state.password
                ):
                    self.state.logins += 1
                    await self._ok(writer, tag, "LOGIN completed")
                else:
                    await self._write(
                        writer, b"A1 NO [AUTHENTICATIONFAILED] invalid credentials\r\n"
                    )
            elif command == "AUTHENTICATE":
                await self._handle_authenticate(reader, writer, tag, parts)
            elif command == "LIST":
                await self._handle_list(writer, tag)
            elif command in {"SELECT", "EXAMINE"}:
                selected = self._mailbox(parts)
                await self._handle_select(
                    writer,
                    tag,
                    selected,
                    parts,
                    forced_readonly=command == "EXAMINE",
                )
            elif command == "UID" and len(parts) >= 3:
                subcommand = parts[2].upper()
                if subcommand == "SEARCH":
                    await self._handle_uid_search(writer, tag, selected, parts)
                elif subcommand == "FETCH":
                    await self._handle_uid_fetch(writer, tag, selected, parts)
                else:
                    self.state.mutated.append(text)
                    await self._write(writer, f"{tag} BAD unsupported UID command\r\n".encode())
            elif command in {"STORE", "EXPUNGE", "COPY", "MOVE", "APPEND", "CLOSE"}:
                self.state.mutated.append(text)
                await self._write(writer, f"{tag} BAD mutating command not supported\r\n".encode())
            elif command == "NOOP":
                await self._ok(writer, tag, "NOOP completed")
            elif command == "LOGOUT":
                await self._write(writer, b"* BYE logging out\r\n")
                await self._ok(writer, tag, "LOGOUT completed")
                return
            elif command in {"IDLE", "STARTTLS", "AUTHENTICATE-PLAIN"}:
                self.state.mutated.append(text)
                await self._write(writer, f"{tag} BAD unsupported command\r\n".encode())
            else:
                await self._write(writer, f"{tag} BAD unknown command\r\n".encode())

    async def _handle_authenticate(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        tag: str,
        parts: list[str],
    ) -> None:
        mechanism = parts[2].upper() if len(parts) > 2 else ""
        if mechanism != "PLAIN":
            await self._write(writer, f"{tag} NO unsupported mechanism\r\n".encode())
            return
        await self._write(writer, b"+ \r\n")
        line = await reader.readline()
        try:
            decoded = base64.b64decode(line.strip()).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            await self._write(writer, f"{tag} NO invalid payload\r\n".encode())
            return
        fields = decoded.split("\x00")
        if (
            len(fields) >= 3
            and fields[-2] == self.state.username
            and fields[-1] == self.state.password
        ):
            self.state.logins += 1
            await self._ok(writer, tag, "AUTHENTICATE completed")
        else:
            await self._write(writer, f"{tag} NO authentication failed\r\n".encode())

    async def _handle_list(self, writer: asyncio.StreamWriter, tag: str) -> None:
        for name in self.state.folders:
            attributes = "\\HasNoChildren"
            if name.upper() == "SENT":
                attributes = "\\HasNoChildren \\Sent"
            line = f'* LIST ({attributes}) "/" "{name}"\r\n'
            await self._write(writer, line.encode())
        await self._ok(writer, tag, "LIST completed")

    async def _handle_select(
        self,
        writer: asyncio.StreamWriter,
        tag: str,
        mailbox: str,
        parts: list[str],
        *,
        forced_readonly: bool = False,
    ) -> None:
        readonly = forced_readonly or any(
            part.upper() == "(READONLY)" for part in parts
        ) or any(part.upper() == "READONLY" for part in parts)
        self.state.select_readonly[mailbox] = readonly
        if mailbox not in self.state.folders:
            await self._write(writer, f"{tag} NO mailbox not found\r\n".encode())
            return
        exists = len(self.state.folders[mailbox])
        uidvalidity = self.state.uidvalidity.get(mailbox, 1)
        await self._write(writer, b"* FLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)\r\n")
        await self._write(writer, f"* {exists} EXISTS\r\n".encode())
        await self._write(writer, f"* OK [UIDVALIDITY {uidvalidity}] UIDs valid\r\n".encode())
        mode = "READ-ONLY" if readonly else "READ-WRITE"
        await self._write(writer, f"{tag} OK [{mode}] SELECT completed\r\n".encode())

    async def _handle_uid_search(
        self,
        writer: asyncio.StreamWriter,
        tag: str,
        selected: str | None,
        parts: list[str],
    ) -> None:
        if selected is None:
            await self._write(writer, f"{tag} NO no mailbox selected\r\n".encode())
            return
        messages = self.state.folders.get(selected, [])
        criteria = " ".join(parts[3:]).strip()
        if criteria.upper().startswith("HEADER MESSAGE-ID"):
            match = re.search(r'"([^"]+)"', criteria)
            needle = match.group(1) if match else ""
            uids = [
                item.uid
                for item in messages
                if needle
                and (
                    f"Message-ID: {needle}".lower()
                    in item.raw.decode("latin-1", "replace").lower()
                    or f"message-id:{needle}".lower()
                    in item.raw.decode("latin-1", "replace").lower()
                )
            ]
        else:
            match = re.search(r"UID (\d+):\*", criteria)
            start = int(match.group(1)) if match else 1
            uids = [item.uid for item in messages if item.uid >= start]
        joined = " ".join(str(uid) for uid in uids)
        await self._write(writer, f"* SEARCH {joined}\r\n".encode())
        await self._ok(writer, tag, "UID SEARCH completed")

    async def _handle_uid_fetch(
        self,
        writer: asyncio.StreamWriter,
        tag: str,
        selected: str | None,
        parts: list[str],
    ) -> None:
        if selected is None:
            await self._write(writer, f"{tag} NO no mailbox selected\r\n".encode())
            return
        uid = parts[3] if len(parts) > 3 else "0"
        spec = " ".join(parts[4:]).upper()
        message = next(
            (item for item in self.state.folders.get(selected, []) if str(item.uid) == uid),
            None,
        )
        if message is None:
            await self._ok(writer, tag, "UID FETCH completed")
            return
        flags = " ".join(message.flags)
        internal = "18-Sep-2026 09:00:00 +0000"
        if "BODY.PEEK[]" in spec or "BODY[]" in spec:
            header = (
                f"* 1 FETCH (UID {message.uid} FLAGS ({flags}) "
                f'INTERNALDATE "{internal}" RFC822.SIZE {len(message.raw)} '
                f"BODY[] {{{len(message.raw)}}}\r\n"
            ).encode()
            await self._write(writer, header + message.raw + b")\r\n")
        elif "BODY.PEEK[HEADER]" in spec or "BODY[HEADER]" in spec:
            header_bytes = message.raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            head = (
                f"* 1 FETCH (UID {message.uid} BODY[HEADER] "
                f"{{{len(header_bytes)}}}\r\n"
            ).encode()
            await self._write(writer, head + header_bytes + b")\r\n")
        else:
            header = (
                f"* 1 FETCH (UID {message.uid} FLAGS ({flags}) "
                f'INTERNALDATE "{internal}" RFC822.SIZE {len(message.raw)})\r\n'
            ).encode()
            await self._write(writer, header)
        await self._ok(writer, tag, "UID FETCH completed")

    async def _ok(self, writer: asyncio.StreamWriter, tag: str, message: str) -> None:
        await self._write(writer, f"{tag} OK {message}\r\n".encode())

    @staticmethod
    async def _write(writer: asyncio.StreamWriter, data: bytes) -> None:
        writer.write(data)
        await writer.drain()

    @staticmethod
    def _mailbox(parts: list[str]) -> str:
        if len(parts) < 3:
            return "INBOX"
        value = " ".join(parts[2:])
        if value.upper().endswith(" (READONLY)"):
            value = value[: -len(" (READONLY)")]
        if value.upper().endswith(" READONLY"):
            value = value[: -len(" READONLY")]
        return value.strip().strip('"')


@dataclass(slots=True)
class SmtpState:
    username: str = "student@smail.nju.edu.cn"
    password: str = "client-password-1"
    #: address -> SMTP code (250 accepts, 550 rejects)
    recipient_policy: dict[str, int] = field(default_factory=dict)
    reject_sender: bool = False
    reject_data: bool = False
    disconnect: str | None = None  # "before_data" | "during_data" | "after_data"
    #: "greeting" | "rcpt" | "after_data": keep the socket open and never reply
    stall: str | None = None
    #: Hold the RCPT reply / the post-DATA reply to open a deterministic window
    #: for registry mutations while the sender is mid-transaction.
    rcpt_delay_seconds: float = 0.0
    data_delay_seconds: float = 0.0
    rcpt_seen: asyncio.Event = field(default_factory=asyncio.Event)
    data_seen: asyncio.Event = field(default_factory=asyncio.Event)
    connections: int = 0
    messages: list[bytes] = field(default_factory=list)
    recipients: list[list[str]] = field(default_factory=list)
    auth_failures: int = 0
    auth_attempts: int = 0


class SimulatedSmtpServer:
    """Implicit-TLS SMTP server with scriptable failure phases."""

    def __init__(self, state: SmtpState, ssl_context: ssl.SSLContext) -> None:
        self.state = state
        self._ssl_context = ssl_context
        self._server: asyncio.AbstractServer | None = None
        self.port = 0
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, host="127.0.0.1", port=0, ssl=self._ssl_context
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await self._serve(reader, writer)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.state.connections += 1
        if self.state.stall == "greeting":
            await asyncio.sleep(3600)
            return
        await self._write(writer, b"220 smtp.test ESMTP ready\r\n")
        envelope_recipients: list[str] = []
        while True:
            line = await reader.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            command = text.upper()
            if command.startswith("EHLO") or command.startswith("HELO"):
                await self._write(
                    writer,
                    b"250-smtp.test\r\n"
                    b"250-AUTH PLAIN LOGIN\r\n"
                    b"250-8BITMIME\r\n"
                    b"250 SIZE 10485760\r\n",
                )
            elif command.startswith("AUTH PLAIN"):
                self.state.auth_attempts += 1
                token = text.split(" ", 2)[2] if len(text.split(" ", 2)) > 2 else ""
                if _smtp_auth(token, self.state):
                    await self._write(writer, b"235 2.7.0 authentication successful\r\n")
                else:
                    self.state.auth_failures += 1
                    await self._write(writer, b"535 5.7.8 authentication failed\r\n")
            elif command.startswith("AUTH LOGIN"):
                self.state.auth_attempts += 1
                await self._write(writer, b"334 VXNlcm5hbWU6\r\n")
                user = (await reader.readline()).strip()
                await self._write(writer, b"334 UGFzc3dvcmQ6\r\n")
                password = (await reader.readline()).strip()
                if (
                    base64.b64decode(user).decode("utf-8") == self.state.username
                    and base64.b64decode(password).decode("utf-8") == self.state.password
                ):
                    await self._write(writer, b"235 2.7.0 authentication successful\r\n")
                else:
                    self.state.auth_failures += 1
                    await self._write(writer, b"535 5.7.8 authentication failed\r\n")
            elif command.startswith("MAIL FROM"):
                if self.state.reject_sender:
                    await self._write(writer, b"550 5.1.0 sender rejected\r\n")
                else:
                    await self._write(writer, b"250 2.1.0 sender ok\r\n")
            elif command.startswith("RCPT TO"):
                if self.state.stall == "rcpt":
                    await asyncio.sleep(3600)
                    return
                recipient = text.partition(":")[2].strip().strip("<>")
                code = self.state.recipient_policy.get(recipient.lower())
                if self.state.disconnect == "before_data":
                    return
                if code is None:
                    code = 250
                envelope_recipients.append(recipient)
                self.state.rcpt_seen.set()
                if self.state.rcpt_delay_seconds > 0:
                    await asyncio.sleep(self.state.rcpt_delay_seconds)
                await self._write(writer, f"{code} 2.1.5 recipient\r\n".encode())
            elif command == "DATA":
                if self.state.disconnect == "during_data":
                    return
                await self._write(writer, b"354 end with <CRLF>.<CRLF>\r\n")
                self.state.data_seen.set()
                payload = await _read_smtp_data(reader)
                if self.state.stall == "after_data":
                    await asyncio.sleep(3600)
                    return
                if self.state.disconnect == "after_data":
                    return
                self.state.messages.append(payload)
                self.state.recipients.append(list(envelope_recipients))
                if self.state.data_delay_seconds > 0:
                    await asyncio.sleep(self.state.data_delay_seconds)
                if self.state.reject_data:
                    await self._write(writer, b"554 5.3.0 message rejected\r\n")
                else:
                    await self._write(writer, b"250 2.0.0 queued as TEST123\r\n")
            elif command == "QUIT":
                await self._write(writer, b"221 2.0.0 bye\r\n")
                return
            elif command == "RSET":
                envelope_recipients.clear()
                await self._write(writer, b"250 2.0.0 reset\r\n")
            else:
                await self._write(writer, b"500 5.5.1 unknown command\r\n")

    @staticmethod
    async def _write(writer: asyncio.StreamWriter, data: bytes) -> None:
        writer.write(data)
        await writer.drain()


def _smtp_auth(token: str, state: SmtpState) -> bool:
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    fields = decoded.split("\x00")
    return len(fields) >= 3 and fields[-2] == state.username and fields[-1] == state.password


async def _read_smtp_data(reader: asyncio.StreamReader) -> bytes:
    lines: list[bytes] = []
    while True:
        line = await reader.readline()
        if not line:
            return b""
        if line in {b".\r\n", b".\n"}:
            break
        if line.startswith(b".."):
            line = line[1:]
        lines.append(line)
    return b"".join(lines)




class SimulatedTcpStallServer:
    """Accepts TCP connections and never performs a TLS handshake.

    Used to prove that a timeout or cancellation during connect/TLS still closes
    the transport and reclaims the worker thread.
    """

    def __init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None
        self.port = 0
        self.connections = 0
        self.received = 0
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, host="127.0.0.1", port=0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        if self._server is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            with contextlib.suppress(Exception):
                while True:
                    chunk = await reader.read(4096)
                    if not chunk:
                        break
                    self.received += len(chunk)
                    await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return
        finally:
            with contextlib.suppress(Exception):
                writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


__all__ = [
    "ImapMessage",
    "SimulatedTcpStallServer",
    "ImapState",
    "SimulatedImapServer",
    "SimulatedSmtpServer",
    "SmtpState",
    "TlsPair",
    "generate_tls_pair",
]
