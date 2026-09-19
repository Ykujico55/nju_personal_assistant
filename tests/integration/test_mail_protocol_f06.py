"""F06 protocol integration: real TLS sockets against simulated IMAP/SMTP.

These tests exercise the real client code paths (imaplib/smtplib, certificate
and hostname verification, read-only SELECT, BODY.PEEK, DATA-phase handling)
against local TLS servers.  They are protocol-level evidence; they are not a
substitute for a real smail account, which the project does not have.
"""

from __future__ import annotations

import asyncio
import hashlib
import ssl
import tempfile
import unittest
from pathlib import Path
from typing import Any

from personal_assistant.core.mail import (
    MailAccountRecord,
    MailDeliveryRequest,
    MailDeliveryStatus,
    MailError,
    MailErrorCode,
    MailPolicy,
    MailReconciliationStatus,
)
from personal_assistant.core.secrets import SecretHandle
from personal_assistant.infrastructure.mail.broker import (
    ConfiguredMailTransportBroker,
    binding_for,
)
from personal_assistant.infrastructure.mail.mime import build_message_bytes
from personal_assistant.infrastructure.mail.registry import (
    FileMailAccountRegistry,
    InMemoryMailAccountRegistry,
)
from personal_assistant.infrastructure.mail.smtp_client import TlsSmtpSender
from personal_assistant.infrastructure.memory import InMemorySecretStore
from tests.support.mail_servers import (
    ImapMessage,
    ImapState,
    SimulatedImapServer,
    SimulatedSmtpServer,
    SimulatedTcpStallServer,
    SmtpState,
    TlsPair,
    generate_tls_pair,
)

USERNAME = "student@smail.nju.edu.cn"
PASSWORD = "client-password-f06"
GOOD = "good@example.test"
BAD = "bad@example.test"


def raw_message(message_id: str, subject: str, body: str = "Hello") -> bytes:
    return (
        f"From: sender@example.test\r\n"
        f"To: {USERNAME}\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: {message_id}\r\n"
        f"Date: Fri, 18 Sep 2026 09:00:00 +0000\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}"
    ).encode()


class _BlockingSecretStore:
    """Delegates to the real store but blocks inside credential resolution."""

    def __init__(self, inner: InMemorySecretStore) -> None:
        self._inner = inner
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def put(self, *, name: str, kind: str, value: str) -> SecretHandle:
        return await self._inner.put(name=name, kind=kind, value=value)

    async def resolve_for_broker(self, handle: SecretHandle, *, purpose: str) -> str:
        self.entered.set()
        await self.release.wait()
        return await self._inner.resolve_for_broker(handle, purpose=purpose)

    async def delete(self, handle: SecretHandle) -> None:
        await self._inner.delete(handle)


def _replace_all_in_thread(
    registry: FileMailAccountRegistry, records: tuple[MailAccountRecord, ...]
) -> None:
    """Run a file-registry mutation on a worker thread (writer must block)."""

    asyncio.run(registry.replace_all(records))


class MailProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tls: TlsPair = generate_tls_pair()
        self.secrets = InMemorySecretStore()
        self.handle = await self.secrets.put(
            name="smail", kind="mail_client_password", value=PASSWORD
        )
        self.imap_state = ImapState(username=USERNAME, password=PASSWORD)
        self.imap_state.folders["INBOX"] = [
            ImapMessage(uid=1, raw=raw_message("<one@example.test>", "First")),
            ImapMessage(uid=2, raw=raw_message("<two@example.test>", "Second")),
        ]
        self.imap_state.uidvalidity["INBOX"] = 11
        self.imap_state.folders["Sent"] = []
        self.imap_state.uidvalidity["Sent"] = 21
        self.imap = SimulatedImapServer(self.imap_state, self.tls.server_context)
        await self.imap.start()
        self.smtp_state = SmtpState(username=USERNAME, password=PASSWORD)
        self.smtp = SimulatedSmtpServer(self.smtp_state, self.tls.server_context)
        await self.smtp.start()

    async def asyncTearDown(self) -> None:
        await self.imap.stop()
        await self.smtp.stop()

    def account(
        self,
        *,
        imap_port: int | None = None,
        smtp_port: int | None = None,
        secret_handle: SecretHandle | None = None,
    ) -> MailAccountRecord:
        return MailAccountRecord(
            account_id="nju",
            address=USERNAME,
            imap_host="localhost",
            imap_port=imap_port or self.imap.port,
            smtp_host="localhost",
            smtp_port=smtp_port or self.smtp.port,
            secret_handle_id=(secret_handle or self.handle).id,
            send_enabled=True,
            tls_mode="implicit",
        )

    def broker(
        self,
        *,
        accounts: InMemoryMailAccountRegistry | None = None,
        policy: MailPolicy | None = None,
        ssl_context: ssl.SSLContext | None = None,
        secrets: Any = None,
    ) -> ConfiguredMailTransportBroker:
        registry = accounts or InMemoryMailAccountRegistry((self.account(),))
        return ConfiguredMailTransportBroker(
            secrets or self.secrets,
            registry,
            policy=policy
            or MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
            ssl_context=ssl_context or self.tls.client_context,
            timeout_seconds=5.0,
        )

    def registry(self, *accounts: Any) -> InMemoryMailAccountRegistry:
        return InMemoryMailAccountRegistry(tuple(accounts) or (self.account(),))

    # -- IMAP ---------------------------------------------------------------

    async def test_probe_lists_folders_and_reads_without_mutating_state(self) -> None:
        account = self.account()
        session = await self.broker().read_session(account.account_id)
        try:
            capabilities = await session.probe()
            folders = await session.list_folders()
            first = await session.fetch(
                "INBOX", uidvalidity=11, start_uid=1, limit=10
            )
            second = await session.fetch(
                "INBOX", uidvalidity=11, start_uid=1, limit=10
            )
        finally:
            await session.close()
        self.assertIn("IMAP4REV1", capabilities.imap_capabilities)
        self.assertIn("PLAIN", capabilities.auth_mechanisms)
        self.assertEqual(11, capabilities.uidvalidity)
        self.assertEqual({"INBOX", "Sent"}, {item.name for item in folders})
        self.assertEqual([1, 2], [item.uid for item in first.messages])
        self.assertEqual([1, 2], [item.uid for item in second.messages])
        # Read-only SELECT everywhere; no mutating command was ever issued and
        # the server-side flags are byte-identical.
        self.assertTrue(all(self.imap_state.select_readonly.values()))
        self.assertEqual([], self.imap_state.mutated)
        self.assertEqual(("\\Seen",), self.imap_state.folders["INBOX"][0].flags)
        self.assertEqual(("\\Seen",), self.imap_state.folders["INBOX"][1].flags)

    async def test_uidvalidity_change_is_reported_for_safe_rebuild(self) -> None:
        account = self.account()
        self.imap_state.uidvalidity["INBOX"] = 12
        session = await self.broker().read_session(account.account_id)
        try:
            result = await session.fetch(
                "INBOX", uidvalidity=11, start_uid=1, limit=10
            )
        finally:
            await session.close()
        self.assertEqual(12, result.uidvalidity)
        self.assertEqual((), result.messages)

    async def test_wrong_password_is_a_typed_user_action_error(self) -> None:
        other = await self.secrets.put(
            name="smail", kind="mail_client_password", value="wrong-password"
        )
        account = self.account(secret_handle=other)
        with self.assertRaises(MailError) as captured:
            session = await self.broker(accounts=self.registry(account)).read_session(
                account.account_id
            )
            try:
                await session.probe()
            finally:
                await session.close()
        self.assertEqual(MailErrorCode.AUTH_FAILED, captured.exception.code)
        self.assertTrue(captured.exception.needs_user_action)
        text = repr(captured.exception) + str(captured.exception)
        self.assertNotIn("wrong-password", text)
        self.assertNotIn(PASSWORD, text)

    async def test_unsupported_auth_mechanism_is_needs_user_action(self) -> None:
        self.imap_state.capabilities = ("IMAP4rev1", "AUTH=CRAM-MD5", "LOGINDISABLED")
        account = self.account()
        with self.assertRaises(MailError) as captured:
            session = await self.broker().read_session(account.account_id)
            try:
                await session.probe()
            finally:
                await session.close()
        self.assertEqual(MailErrorCode.AUTH_UNSUPPORTED, captured.exception.code)

    async def test_untrusted_certificate_fails_closed(self) -> None:
        account = self.account()
        with self.assertRaises(MailError) as captured:
            session = await self.broker(
                ssl_context=ssl.create_default_context()
            ).read_session(account.account_id)
            try:
                await session.probe()
            finally:
                await session.close()
        self.assertEqual(MailErrorCode.TLS_FAILED, captured.exception.code)

    async def test_slow_trickle_respects_the_total_deadline(self) -> None:
        self.imap_state.stall = True
        account = self.account()
        broker = ConfiguredMailTransportBroker(
            self.secrets,
            InMemoryMailAccountRegistry((self.account(),)),
            policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
            ssl_context=self.tls.client_context,
            timeout_seconds=0.5,
        )
        with self.assertRaises(MailError) as captured:
            session = await broker.read_session(account.account_id)
            try:
                await session.probe()
            finally:
                await session.close()
        self.assertEqual(MailErrorCode.TIMEOUT, captured.exception.code)
        self.assertEqual(1, self.imap_state.connections)

    async def test_cancellation_closes_the_session(self) -> None:
        self.imap_state.stall = True
        account = self.account()
        broker = ConfiguredMailTransportBroker(
            self.secrets,
            InMemoryMailAccountRegistry((self.account(),)),
            policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
            ssl_context=self.tls.client_context,
            timeout_seconds=30.0,
        )
        session = await broker.read_session(account.account_id)
        task = asyncio.create_task(session.probe())
        await asyncio.sleep(0.3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(session._connection)  # noqa: SLF001 - cancel must abort
        await session.close()

    async def test_revoked_credential_fails_closed(self) -> None:
        missing = SecretHandle(id="not-stored", kind="mail_client_password")
        account = self.account(secret_handle=missing)
        with self.assertRaises(MailError) as captured:
            session = await self.broker(accounts=self.registry(account)).read_session(
                account.account_id
            )
            try:
                await session.probe()
            finally:
                await session.close()
        self.assertEqual(MailErrorCode.CREDENTIAL_UNAVAILABLE, captured.exception.code)
        self.assertTrue(captured.exception.needs_user_action)

    # -- SMTP ---------------------------------------------------------------

    def delivery_request(self) -> MailDeliveryRequest:
        message_id = "<smail.act-test@example.test>"
        raw = build_message_bytes(
            message_id=message_id,
            from_address=USERNAME,
            to_addresses=(GOOD,),
            cc_addresses=(),
            bcc_addresses=(),
            subject="Protocol",
            body_text="Body",
        )
        return MailDeliveryRequest(
            local_action_id="act-test-0001",
            message_id=message_id,
            from_address=USERNAME,
            to=(GOOD,),
            subject="Protocol",
            mime_bytes=raw,
        )

    async def test_successful_send_uses_exact_bytes(self) -> None:
        request = self.delivery_request()
        receipt = await self.broker().send(self.account().account_id, request)
        self.assertEqual(MailDeliveryStatus.SUCCEEDED, receipt.status)
        self.assertEqual([GOOD], list(receipt.accepted))
        self.assertEqual(1, len(self.smtp_state.messages))
        self.assertEqual(request.mime_bytes, self.smtp_state.messages[0])
        self.assertEqual(1, self.smtp_state.auth_attempts)
        self.assertEqual(0, self.smtp_state.auth_failures)

    async def test_partial_recipient_rejection_keeps_full_detail(self) -> None:
        self.smtp_state.recipient_policy[BAD] = 550
        request = MailDeliveryRequest(
            local_action_id="act-test-0002",
            message_id="<smail.act-test-2@example.test>",
            from_address=USERNAME,
            to=(GOOD,),
            bcc=(BAD,),
            subject="Partial",
            mime_bytes=build_message_bytes(
                message_id="<smail.act-test-2@example.test>",
                from_address=USERNAME,
                to_addresses=(GOOD,),
                cc_addresses=(),
                bcc_addresses=(BAD,),
                subject="Partial",
                body_text="Body",
            ),
        )
        receipt = await self.broker(
            policy=MailPolicy(
                allow_send=True, allowed_recipients=frozenset({GOOD, BAD})
            )
        ).send(self.account().account_id, request)
        self.assertEqual(MailDeliveryStatus.PARTIAL, receipt.status)
        results = {item.recipient: item.status.value for item in receipt.recipient_results}
        self.assertEqual({"good@example.test": "ACCEPTED", "bad@example.test": "REJECTED"}, results)
        self.assertEqual(1, len(self.smtp_state.messages))

    async def test_data_rejection_is_a_definitive_failure(self) -> None:
        self.smtp_state.reject_data = True
        receipt = await self.broker().send(self.account().account_id, self.delivery_request())
        self.assertEqual(MailDeliveryStatus.FAILED, receipt.status)

    async def test_disconnect_before_data_is_a_definitive_failure(self) -> None:
        self.smtp_state.disconnect = "before_data"
        receipt = await self.broker().send(self.account().account_id, self.delivery_request())
        self.assertEqual(MailDeliveryStatus.FAILED, receipt.status)

    async def test_disconnect_during_data_is_unknown(self) -> None:
        self.smtp_state.disconnect = "during_data"
        receipt = await self.broker().send(self.account().account_id, self.delivery_request())
        self.assertEqual(MailDeliveryStatus.UNKNOWN, receipt.status)

    async def test_disconnect_after_data_is_unknown(self) -> None:
        self.smtp_state.disconnect = "after_data"
        receipt = await self.broker().send(self.account().account_id, self.delivery_request())
        self.assertEqual(MailDeliveryStatus.UNKNOWN, receipt.status)

    async def test_allowlist_denies_unknown_recipients_before_connecting(self) -> None:
        account = self.account()
        request = MailDeliveryRequest(
            local_action_id="act-test-allow",
            message_id="<smail.allow@example.test>",
            from_address=USERNAME,
            to=("stranger@example.test",),
            subject="Nope",
            mime_bytes=b"x",
        )
        with self.assertRaises(MailError) as captured:
            await self.broker(accounts=self.registry(account)).send(
                account.account_id, request
            )
        self.assertEqual(MailErrorCode.RECIPIENT_NOT_ALLOWED, captured.exception.code)
        self.assertEqual([], self.smtp_state.messages)

    async def test_send_disabled_by_host_policy(self) -> None:
        broker = self.broker(policy=MailPolicy(allow_send=False))
        with self.assertRaises(MailError) as captured:
            await broker.send(self.account().account_id, self.delivery_request())
        self.assertEqual(MailErrorCode.SEND_DISABLED, captured.exception.code)
        self.assertEqual([], self.smtp_state.messages)

    async def test_bad_smtp_credentials_are_typed(self) -> None:
        other = await self.secrets.put(
            name="smail", kind="mail_client_password", value="wrong-password"
        )
        bad_account = self.account(secret_handle=other)
        with self.assertRaises(MailError) as captured:
            await self.broker(accounts=self.registry(bad_account)).send(
                bad_account.account_id, self.delivery_request()
            )
        self.assertEqual(MailErrorCode.AUTH_FAILED, captured.exception.code)
        self.assertEqual([], self.smtp_state.messages)


    async def test_smtp_timeout_before_data_is_a_definitive_failure(self) -> None:
        self.smtp_state.stall = "rcpt"
        sender = TlsSmtpSender(
            binding_for(self.account()),
            PASSWORD,
            policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
            ssl_context=self.tls.client_context,
            timeout_seconds=0.5,
        )
        receipt = await sender.send(self.delivery_request())
        self.assertEqual(MailDeliveryStatus.FAILED, receipt.status)
        self.assertEqual(0, sender.running_threads)

    async def test_smtp_timeout_during_data_is_unknown_and_thread_stops(self) -> None:
        self.smtp_state.stall = "after_data"
        sender = TlsSmtpSender(
            binding_for(self.account()),
            PASSWORD,
            policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
            ssl_context=self.tls.client_context,
            timeout_seconds=0.5,
        )
        receipt = await sender.send(self.delivery_request())
        self.assertEqual(MailDeliveryStatus.UNKNOWN, receipt.status)
        # The socket was closed and the worker thread actually exited before the
        # UNKNOWN result was returned.
        self.assertEqual(0, sender.running_threads)
        self.assertIsNone(sender._active)  # noqa: SLF001
        self.assertIn(sender.phase, {"data", "post_data"})
        self.assertEqual(1, self.smtp_state.connections)

    async def test_repeated_cancellation_still_reaps_the_thread(self) -> None:
        self.smtp_state.stall = "after_data"
        sender = TlsSmtpSender(
            binding_for(self.account()),
            PASSWORD,
            policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
            ssl_context=self.tls.client_context,
            timeout_seconds=30.0,
        )
        task = asyncio.create_task(sender.send(self.delivery_request()))
        await asyncio.sleep(0.3)
        task.cancel()
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(0, sender.running_threads)
        self.assertIsNone(sender._active)  # noqa: SLF001


    async def test_timeout_during_tls_handshake_fails_closed_and_reaps(self) -> None:
        stall = SimulatedTcpStallServer()
        await stall.start()
        try:
            account = self.account(smtp_port=stall.port)
            sender = TlsSmtpSender(
                binding_for(account),
                PASSWORD,
                policy=MailPolicy(
                    allow_send=True, allowed_recipients=frozenset({GOOD})
                ),
                ssl_context=self.tls.client_context,
                timeout_seconds=0.5,
            )
            receipt = await sender.send(self.delivery_request())
            self.assertEqual(MailDeliveryStatus.FAILED, receipt.status)
            self.assertEqual(0, sender.running_threads)
            self.assertIsNone(sender._active)  # noqa: SLF001
            self.assertTrue(sender._abort.is_set())  # noqa: SLF001
            self.assertGreaterEqual(stall.connections, 1)
            self.assertEqual([], self.smtp_state.messages)
        finally:
            await stall.stop()

    async def test_cancellation_during_tls_handshake_closes_and_reaps(self) -> None:
        stall = SimulatedTcpStallServer()
        await stall.start()
        try:
            account = self.account(smtp_port=stall.port)
            sender = TlsSmtpSender(
                binding_for(account),
                PASSWORD,
                policy=MailPolicy(
                    allow_send=True, allowed_recipients=frozenset({GOOD})
                ),
                ssl_context=self.tls.client_context,
                timeout_seconds=30.0,
            )
            task = asyncio.create_task(sender.send(self.delivery_request()))
            for _ in range(50):
                if stall.connections:
                    break
                await asyncio.sleep(0.02)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(0, sender.running_threads)
            self.assertIsNone(sender._active)  # noqa: SLF001
            self.assertEqual([], self.smtp_state.messages)
        finally:
            await stall.stop()

    async def test_completed_send_wins_over_deadline_classification(self) -> None:
        """A just-in-time 250 must never be reclassified as FAILED by a timeout."""

        sender = TlsSmtpSender(
            binding_for(self.account()),
            PASSWORD,
            policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
            ssl_context=self.tls.client_context,
            timeout_seconds=5.0,
        )
        request = self.delivery_request()
        receipt = await sender.send(request)
        self.assertEqual(MailDeliveryStatus.SUCCEEDED, receipt.status)
        self.assertEqual("done", sender.phase)
        # The thread actually completed first; the timeout resolver must return
        # the stored real receipt instead of an aborted FAILED classification.
        resolved = sender._timeout_receipt(request)  # noqa: SLF001
        self.assertEqual(MailDeliveryStatus.SUCCEEDED, resolved.status)
        self.assertEqual(0, sender.running_threads)



    async def test_rebind_during_credential_resolution_blocks_dispatch(self) -> None:
        from dataclasses import replace

        from personal_assistant.core.mail import MailError, MailErrorCode

        account = self.account()
        registry = InMemoryMailAccountRegistry((account,))
        blocking = _BlockingSecretStore(self.secrets)
        broker = ConfiguredMailTransportBroker(
            blocking,
            registry,
            policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
            ssl_context=self.tls.client_context,
            timeout_seconds=5.0,
        )
        task = asyncio.create_task(broker.send(account.account_id, self.delivery_request()))
        await blocking.entered.wait()
        # The user re-binds the account while the credential backend is awaited.
        await registry.replace_all(
            (replace(account, secret_handle_id="other-handle"),)
        )
        blocking.release.set()
        with self.assertRaises(MailError) as captured:
            await task
        self.assertEqual(MailErrorCode.ACCOUNT_CHANGED, captured.exception.code)
        self.assertEqual(0, self.smtp_state.connections)
        self.assertEqual([], self.smtp_state.messages)

    async def test_dns_stall_respects_deadline(self) -> None:
        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo

        async def _stall(*args: object, **kwargs: object) -> object:
            await asyncio.sleep(30)
            return []

        loop.getaddrinfo = _stall  # type: ignore[method-assign]
        try:
            account = self.account()
            sender = TlsSmtpSender(
                binding_for(account),
                PASSWORD,
                policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
                ssl_context=self.tls.client_context,
                timeout_seconds=0.5,
            )
            receipt = await sender.send(self.delivery_request())
            self.assertEqual(MailDeliveryStatus.FAILED, receipt.status)
            self.assertEqual(0, sender.running_threads)
            self.assertEqual(0, self.smtp_state.connections)
        finally:
            loop.getaddrinfo = original  # type: ignore[method-assign]

    async def test_dns_stall_cancellation_returns_without_thread(self) -> None:
        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo

        async def _stall(*args: object, **kwargs: object) -> object:
            await asyncio.sleep(30)
            return []

        loop.getaddrinfo = _stall  # type: ignore[method-assign]
        try:
            account = self.account()
            sender = TlsSmtpSender(
                binding_for(account),
                PASSWORD,
                policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
                ssl_context=self.tls.client_context,
                timeout_seconds=30.0,
            )
            task = asyncio.create_task(sender.send(self.delivery_request()))
            await asyncio.sleep(0.2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(0, sender.running_threads)
            self.assertEqual(0, self.smtp_state.connections)
        finally:
            loop.getaddrinfo = original  # type: ignore[method-assign]

    async def test_lost_lease_guard_aborts_before_data(self) -> None:
        class _InvalidGuard:
            @property
            def valid(self) -> bool:
                return False

        account = self.account()
        sender = TlsSmtpSender(
            binding_for(account),
            PASSWORD,
            policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
            ssl_context=self.tls.client_context,
            timeout_seconds=5.0,
            guard=_InvalidGuard(),
        )
        receipt = await sender.send(self.delivery_request())
        self.assertEqual(MailDeliveryStatus.FAILED, receipt.status)
        self.assertEqual([], self.smtp_state.messages)
        self.assertEqual([], self.smtp_state.recipients)


    async def test_rebind_after_reverify_aborts_before_data(self) -> None:
        from dataclasses import replace

        account = self.account()
        registry = InMemoryMailAccountRegistry((account,))
        broker = ConfiguredMailTransportBroker(
            self.secrets,
            registry,
            policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
            ssl_context=self.tls.client_context,
            timeout_seconds=5.0,
        )
        self.smtp_state.rcpt_delay_seconds = 0.6
        task = asyncio.create_task(
            broker.send(account.account_id, self.delivery_request())
        )
        await asyncio.wait_for(self.smtp_state.rcpt_seen.wait(), timeout=5.0)
        # Re-bind after the broker's post-credential re-verify and while the
        # sender is blocked on RCPT, immediately before DATA.
        await registry.replace_all((replace(account, secret_handle_id="other-handle"),))
        with self.assertRaises(MailError) as captured_error:
            await asyncio.wait_for(task, timeout=5.0)
        self.assertEqual(MailErrorCode.ACCOUNT_CHANGED, captured_error.exception.code)
        self.assertEqual([], self.smtp_state.messages)
        self.assertEqual([], self.smtp_state.recipients)

    async def test_cross_process_registry_rebind_aborts_before_data(self) -> None:
        from dataclasses import replace

        account = self.account()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mail_accounts.json"
            writer = FileMailAccountRegistry(path)
            await writer.upsert(account)
            reader = FileMailAccountRegistry(path)
            broker = ConfiguredMailTransportBroker(
                self.secrets,
                reader,
                policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
                ssl_context=self.tls.client_context,
                timeout_seconds=5.0,
            )
            self.smtp_state.rcpt_delay_seconds = 0.6
            task = asyncio.create_task(
                broker.send(account.account_id, self.delivery_request())
            )
            await asyncio.wait_for(self.smtp_state.rcpt_seen.wait(), timeout=5.0)
            # A different process incarnation (separate registry instance, same
            # file) re-binds the account while the sender is mid-RCPT: the live
            # guard must see the change before DATA and abort typed.
            await writer.replace_all(
                (replace(account, secret_handle_id="other-handle"),)
            )
            with self.assertRaises(MailError) as captured_error:
                await asyncio.wait_for(task, timeout=5.0)
            self.assertEqual(
                MailErrorCode.ACCOUNT_CHANGED, captured_error.exception.code
            )
            self.assertEqual([], self.smtp_state.messages)
            self.assertEqual([], self.smtp_state.recipients)

    async def test_cross_process_writer_waits_while_data_is_in_flight(self) -> None:
        from dataclasses import replace

        account = self.account()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mail_accounts.json"
            sender_registry = FileMailAccountRegistry(path)
            await sender_registry.upsert(account)
            writer_registry = FileMailAccountRegistry(path)
            broker = ConfiguredMailTransportBroker(
                self.secrets,
                sender_registry,
                policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
                ssl_context=self.tls.client_context,
                timeout_seconds=5.0,
            )
            self.smtp_state.data_delay_seconds = 0.5
            task = asyncio.create_task(
                broker.send(account.account_id, self.delivery_request())
            )
            await asyncio.wait_for(self.smtp_state.data_seen.wait(), timeout=5.0)
            writer = asyncio.create_task(
                asyncio.to_thread(
                    _replace_all_in_thread,
                    writer_registry,
                    (replace(account, secret_handle_id="other-handle"),),
                )
            )
            await asyncio.sleep(0.15)
            # DATA holds the cross-process critical section: the writer waits.
            self.assertFalse(writer.done())
            receipt = await asyncio.wait_for(task, timeout=5.0)
            self.assertEqual(MailDeliveryStatus.SUCCEEDED, receipt.status)
            await asyncio.wait_for(writer, timeout=5.0)
            # The already-started submission committed before the mutation.
            self.assertEqual(1, len(self.smtp_state.messages))
            updated = await writer_registry.resolve(account.account_id)
            self.assertEqual("other-handle", updated.secret_handle_id)

    async def test_lease_lost_while_waiting_for_the_account_lock_never_sends(
        self,
    ) -> None:
        from personal_assistant.infrastructure.mail.executor import _LeaseGuard
        from personal_assistant.infrastructure.mail.file_lock import ExclusiveFileLock

        account = self.account()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mail_accounts.json"
            registry = FileMailAccountRegistry(path)
            await registry.upsert(account)
            broker = ConfiguredMailTransportBroker(
                self.secrets,
                registry,
                policy=MailPolicy(allow_send=True, allowed_recipients=frozenset({GOOD})),
                ssl_context=self.tls.client_context,
                timeout_seconds=5.0,
            )
            lock_path = path.with_name(path.name + ".lock")
            holder = ExclusiveFileLock(lock_path)
            self.assertTrue(holder.acquire())
            lease_guard = _LeaseGuard(1.0)
            task = asyncio.create_task(
                broker.send(
                    account.account_id, self.delivery_request(), guard=lease_guard
                )
            )
            try:
                await asyncio.wait_for(self.smtp_state.rcpt_seen.wait(), timeout=5.0)
                # The sender is now blocked on the account lock for DATA while
                # the execution lease expires.
                await asyncio.sleep(1.2)
                self.assertFalse(lease_guard.valid)
            finally:
                holder.release()
            receipt = await asyncio.wait_for(task, timeout=5.0)
            # The composite guard re-verified the expired lease after every
            # lock was held: DATA never started.
            self.assertEqual(MailDeliveryStatus.FAILED, receipt.status)
            self.assertEqual([], self.smtp_state.messages)
            self.assertEqual([], self.smtp_state.recipients)

    # -- reconciliation -----------------------------------------------------

    async def test_sent_reconciliation_matched_not_found_and_ambiguous(self) -> None:
        message_id = "<smail.reconcile@example.test>"
        self.imap_state.folders["Sent"] = [
            ImapMessage(uid=40, raw=raw_message(message_id, "Sent copy"))
        ]
        broker = self.broker()
        matched = await broker.reconcile_sent(
            self.account().account_id, local_action_id="act-r", message_id=message_id
        )
        self.assertEqual(MailReconciliationStatus.MATCHED, matched.status)
        self.assertEqual(1, len(matched.matches))
        self.assertEqual(21, self.imap_state.uidvalidity["Sent"])

        not_found = await broker.reconcile_sent(
            self.account().account_id, local_action_id="act-r", message_id="<missing@example.test>"
        )
        self.assertEqual(MailReconciliationStatus.NOT_FOUND, not_found.status)

        self.imap_state.folders["Sent"].append(
            ImapMessage(uid=41, raw=raw_message(message_id, "Duplicate"))
        )
        ambiguous = await broker.reconcile_sent(
            self.account().account_id, local_action_id="act-r", message_id=message_id
        )
        self.assertEqual(MailReconciliationStatus.AMBIGUOUS, ambiguous.status)
        self.assertTrue(all(self.imap_state.select_readonly.values()))
        self.assertEqual([], self.imap_state.mutated)

    async def test_reconciliation_never_deletes_or_moves(self) -> None:
        message_id = "<smail.keep@example.test>"
        original = ImapMessage(uid=50, raw=raw_message(message_id, "Keep"))
        self.imap_state.folders["Sent"] = [original]
        before = hashlib.sha256(original.raw).hexdigest()
        await self.broker().reconcile_sent(
            self.account().account_id, local_action_id="act-r", message_id=message_id
        )
        self.assertEqual(before, hashlib.sha256(self.imap_state.folders["Sent"][0].raw).hexdigest())
        self.assertEqual(50, self.imap_state.folders["Sent"][0].uid)
        self.assertEqual([], self.imap_state.mutated)


if __name__ == "__main__":
    unittest.main()
