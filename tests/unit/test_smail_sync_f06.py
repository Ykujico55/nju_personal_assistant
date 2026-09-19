"""F06 unit tests: UID/Message-ID/content dedupe identity and cursor semantics."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from nju_smail.sync import _message_insert


@dataclass(frozen=True, slots=True)
class _Fetched:
    uid: int
    raw: bytes
    flags: tuple[str, ...] = ()
    size_bytes: int = 0
    truncated: bool = False


def _raw(
    *, message_id: str | None = "<a@b>", subject: str = "Hello", body: str = "Body"
) -> bytes:
    headers = [
        "From: sender@example.test",
        "To: receiver@example.test",
        f"Subject: {subject}",
        "Date: Fri, 18 Sep 2026 09:00:00 +0000",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
    ]
    if message_id is not None:
        headers.append(f"Message-ID: {message_id}")
    return ("\r\n".join(headers) + "\r\n\r\n" + body).encode("utf-8")


class MessageIdentityTests(unittest.TestCase):
    def test_same_uid_twice_maps_to_one_identity(self) -> None:
        first = _message_insert(
            "acct", "INBOX", 7, _Fetched(uid=10, raw=_raw(), size_bytes=100)
        )
        second = _message_insert(
            "acct", "INBOX", 7, _Fetched(uid=10, raw=_raw(), size_bytes=100)
        )
        self.assertEqual(first.message_pk, second.message_pk)
        self.assertEqual(first.dedupe_key, second.dedupe_key)
        self.assertEqual(10, first.uid)
        self.assertEqual(7, first.uidvalidity)

    def test_same_content_under_different_uids_shares_one_identity(self) -> None:
        first = _message_insert("acct", "INBOX", 7, _Fetched(uid=10, raw=_raw()))
        second = _message_insert("acct", "INBOX", 7, _Fetched(uid=11, raw=_raw()))
        self.assertEqual(first.message_pk, second.message_pk)
        self.assertNotEqual(first.uid, second.uid)

    def test_missing_message_id_falls_back_to_content_hash(self) -> None:
        first = _message_insert("acct", "INBOX", 7, _Fetched(uid=1, raw=_raw(message_id=None)))
        second = _message_insert("acct", "INBOX", 7, _Fetched(uid=2, raw=_raw(message_id=None)))
        third = _message_insert(
            "acct",
            "INBOX",
            7,
            _Fetched(uid=3, raw=_raw(message_id=None, body="Different body")),
        )
        self.assertEqual(first.message_pk, second.message_pk)
        self.assertNotEqual(first.message_pk, third.message_pk)

    def test_reused_message_id_with_new_body_is_a_new_logical_message(self) -> None:
        first = _message_insert("acct", "INBOX", 7, _Fetched(uid=1, raw=_raw(body="one")))
        reused = _message_insert("acct", "INBOX", 7, _Fetched(uid=2, raw=_raw(body="two")))
        self.assertNotEqual(first.message_pk, reused.message_pk)
        self.assertNotEqual(first.dedupe_key, reused.dedupe_key)
        self.assertNotEqual(first.content_hash, reused.content_hash)

    def test_uidvalidity_reset_keeps_identity_and_reattaches_locations(self) -> None:
        before = _message_insert("acct", "INBOX", 7, _Fetched(uid=99, raw=_raw()))
        after = _message_insert("acct", "INBOX", 8, _Fetched(uid=1, raw=_raw()))
        self.assertEqual(before.message_pk, after.message_pk)
        self.assertEqual(8, after.uidvalidity)
        self.assertEqual("smail:acct:" + after.dedupe_key, "smail:acct:" + before.dedupe_key)

    def test_threads_link_by_reply_reference_then_subject(self) -> None:
        original = _message_insert("acct", "INBOX", 7, _Fetched(uid=1, raw=_raw()))
        reply_raw = (
            b"From: sender@example.test\r\n"
            b"To: receiver@example.test\r\n"
            b"Subject: Re: Hello\r\n"
            b"In-Reply-To: <a@b>\r\n"
            b"MIME-Version: 1.0\r\n\r\nReplying"
        )
        reply = _message_insert("acct", "INBOX", 7, _Fetched(uid=2, raw=reply_raw))
        self.assertEqual(reply.thread_id, original.thread_id)

    def test_attachment_provenance_contains_hash_media_type_and_safe_name(self) -> None:
        raw = (
            b"From: sender@example.test\r\n"
            b"To: receiver@example.test\r\n"
            b"Subject: File\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=bb\r\n\r\n"
            b"--bb\r\n"
            b"Content-Type: text/plain\r\n\r\nsee attached\r\n"
            b"--bb\r\n"
            b"Content-Type: application/pdf\r\n"
            b"Content-Disposition: attachment; filename=\"../../secret.pdf\"\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\nAAEC\r\n"
            b"--bb--\r\n"
        )
        insert = _message_insert("acct", "INBOX", 7, _Fetched(uid=5, raw=raw))
        self.assertTrue(insert.has_attachments)
        attachment = insert.attachments[0]
        self.assertEqual("secret.pdf", attachment.filename)
        self.assertEqual("application/pdf", attachment.media_type)
        self.assertEqual(64, len(attachment.sha256))
        self.assertEqual(3, attachment.size_bytes)

    def test_truncated_messages_do_not_collide_with_identical_headers(self) -> None:
        first = _message_insert(
            "acct",
            "INBOX",
            7,
            _Fetched(uid=1, raw=_raw(message_id=None), truncated=True, size_bytes=100),
        )
        second = _message_insert(
            "acct",
            "INBOX",
            7,
            _Fetched(uid=2, raw=_raw(message_id=None), truncated=True, size_bytes=200),
        )
        self.assertNotEqual(first.content_hash, second.content_hash)


class _CursorStore:
    """Store double that fails a chosen chunk and records cursor advances."""

    def __init__(self, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.apply_calls = 0
        self.cursor_updates: list[int] = []
        self.state_row: dict[str, object] | None = None
        self.migrated = False

    async def migrate(self, migrations: object) -> dict[str, object]:
        del migrations
        self.migrated = True
        return {"applied": [1], "skipped": []}

    async def state(self, account_id: str, folder: str) -> dict[str, object] | None:
        del account_id, folder
        return self.state_row

    async def set_state_idle(
        self, account_id: str, folder: str, uidvalidity: int, last_uid: int
    ) -> None:
        del account_id, folder
        self.state_row = {
            "uidvalidity": uidvalidity,
            "last_uid": last_uid,
            "status": "IDLE",
            "failure_count": 0,
            "backoff_until": None,
        }

    async def reset_cursor(self, account_id: str, folder: str, uidvalidity: int) -> None:
        del account_id, folder
        self.state_row = {
            "uidvalidity": uidvalidity,
            "last_uid": 0,
            "status": "RESET",
            "failure_count": 0,
            "backoff_until": None,
        }

    async def set_state_error(self, *args: object, **kwargs: object) -> None:
        current = self.state_row or {}
        self.state_row = {
            "uidvalidity": current.get("uidvalidity", 5),
            "last_uid": current.get("last_uid", 0),
            "status": kwargs.get("status"),
            "error_code": kwargs.get("error_code"),
            "failure_count": kwargs.get("failure_count", 0),
            "backoff_until": kwargs.get("backoff_until"),
        }

    async def record_folder(self, account_id: str, folder: object) -> None:
        del account_id, folder

    async def apply_batch(self, **kwargs: object) -> tuple[int, int]:
        self.apply_calls += 1
        if self.fail_on_call is not None and self.apply_calls == self.fail_on_call:
            raise RuntimeError("simulated transaction failure")
        next_uid = int(kwargs["next_uid"])
        self.cursor_updates.append(next_uid)
        self.state_row = {
            "uidvalidity": int(kwargs["uidvalidity"]),
            "last_uid": next_uid,
            "status": "IDLE",
            "failure_count": 0,
            "backoff_until": None,
        }
        messages = kwargs["messages"]
        return (len(messages), len(messages))


class _CursorHostMail:
    def __init__(self, count: int) -> None:
        self.messages = [
            _Fetched(
                uid=index + 1,
                raw=_raw(message_id=f"<m{index}@example.test>", subject=f"S{index}"),
                size_bytes=100,
            )
            for index in range(count)
        ]
        self.fetch_starts: list[int] = []

    async def account(self, account_id: str) -> object:
        from personal_assistant_sdk import MailAccountInfo

        return MailAccountInfo(
            account_id=account_id,
            address="student@smail.nju.edu.cn",
            display_name="NJU",
            read_enabled=True,
            send_enabled=True,
            fingerprint="f" * 64,
        )

    async def probe(self, account_id: str) -> object:
        del account_id
        from personal_assistant_sdk import MailboxCapabilities

        return MailboxCapabilities(
            imap_capabilities=("IMAP4REV1",),
            auth_mechanisms=("PLAIN",),
            uidvalidity=5,
            exists=len(self.messages),
        )

    async def list_folders(self, account_id: str) -> tuple[object, ...]:
        del account_id
        from personal_assistant_sdk import MailFolderInfo

        return (MailFolderInfo(name="INBOX", delimiter="/", attributes=()),)

    async def fetch(
        self,
        account_id: str,
        folder: str,
        *,
        uidvalidity: int | None,
        start_uid: int,
        limit: int = 100,
    ) -> object:
        del account_id, folder, uidvalidity
        from personal_assistant_sdk import FetchedMailBatch

        self.fetch_starts.append(start_uid)
        messages = tuple(item for item in self.messages if item.uid >= start_uid)[:limit]
        return FetchedMailBatch(uidvalidity=5, exists=len(self.messages), messages=messages)

    async def reconcile_sent(self, account_id: str, **kwargs: object) -> object:
        raise AssertionError("not used")

    async def aclose(self) -> None:
        return None


class ChunkedCursorTests(unittest.IsolatedAsyncioTestCase):
    async def _settings(self) -> object:
        from nju_smail.models import parse_settings

        return parse_settings(
            {"accounts": [{"account_id": "nju"}], "folders": ["INBOX"]}
        )

    async def test_failed_second_chunk_does_not_advance_past_it(self) -> None:
        from nju_smail.sync import MailSynchronizer

        store = _CursorStore(fail_on_call=2)
        host = _CursorHostMail(count=30)
        sync = MailSynchronizer(
            store, host, await self._settings(), migrations=()  # type: ignore[arg-type]
        )
        with self.assertRaises(RuntimeError):
            await sync.sync()
        # First chunk (25 messages) committed; the cursor stopped at its max UID.
        self.assertEqual([25], store.cursor_updates)
        self.assertEqual(25, store.state_row["last_uid"])  # type: ignore[index]

        store.fail_on_call = None
        report = await sync.sync()
        # The next run must resume at 26, not skip the failed chunk.
        self.assertEqual(26, host.fetch_starts[-1])
        self.assertEqual(2, len(store.cursor_updates))
        self.assertEqual(30, store.cursor_updates[-1])
        self.assertEqual(5, report.new_messages)

    async def test_first_chunk_failure_keeps_the_previous_cursor(self) -> None:
        from nju_smail.sync import MailSynchronizer

        store = _CursorStore(fail_on_call=1)
        store.state_row = {
            "uidvalidity": 5,
            "last_uid": 10,
            "status": "IDLE",
            "failure_count": 0,
            "backoff_until": None,
        }
        host = _CursorHostMail(count=30)
        sync = MailSynchronizer(
            store, host, await self._settings(), migrations=()  # type: ignore[arg-type]
        )
        with self.assertRaises(RuntimeError):
            await sync.sync()
        self.assertEqual([], store.cursor_updates)
        self.assertEqual(10, store.state_row["last_uid"])  # type: ignore[index]
        self.assertEqual(11, host.fetch_starts[-1])


if __name__ == "__main__":
    unittest.main()
