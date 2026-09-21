"""F06 regression: host.mail.fetch must fit the worker's 1 MiB input frame.

Real mailboxes returned through JSON-RPC previously overflowed the worker input
limit because every message's raw MIME bytes were base64-encoded into one
response; the worker (and the extension) then misreported it as UNAVAILABLE.
The host now returns a byte-bounded batch and flags that more remain.
"""

from __future__ import annotations

import json
import unittest

from personal_assistant.core.mail import (
    MailAccountRecord,
    MailFetchedMessage,
    MailFetchResult,
)
from personal_assistant.infrastructure.mail.host import (
    HOST_MAIL_FETCH,
    MAX_FETCH_RAW_BYTES,
    MailCapabilityContext,
    MailHostCapability,
)
from personal_assistant.infrastructure.mail.registry import InMemoryMailAccountRegistry

WORKER_FRAME_LIMIT = 1024 * 1024
ACCOUNT = MailAccountRecord(
    account_id="nju",
    address="student@smail.nju.edu.cn",
    imap_host="imap.exmail.qq.com",
    smtp_host="smtp.exmail.qq.com",
    secret_handle_id="handle-1",
    read_enabled=True,
    send_enabled=False,
)


def _message(uid: int, size: int) -> MailFetchedMessage:
    return MailFetchedMessage(
        uid=uid,
        message_id=f"<m{uid}@smail.nju.edu.cn>",
        subject=f"subject {uid}",
        from_address="sender@example.test",
        to_addresses=("student@smail.nju.edu.cn",),
        cc_addresses=(),
        sent_at="2026-09-19T12:00:00+00:00",
        flags=("\\Seen",),
        size_bytes=size,
        raw=b"x" * size,
        truncated=False,
    )


class _FakeSession:
    def __init__(self, messages: tuple[MailFetchedMessage, ...]) -> None:
        self._messages = messages

    async def fetch(
        self,
        folder: str,
        *,
        uidvalidity: int | None,
        start_uid: int,
        limit: int,
    ) -> MailFetchResult:
        del folder, uidvalidity
        selected = tuple(
            item for item in self._messages if item.uid >= start_uid
        )[:limit]
        return MailFetchResult(
            uidvalidity=11, exists=len(self._messages), messages=selected
        )

    async def close(self) -> None:
        return None


class _FakeBroker:
    read_available = True

    def __init__(self, messages: tuple[MailFetchedMessage, ...]) -> None:
        self._messages = messages

    async def read_session(self, account_id: str) -> _FakeSession:
        del account_id
        return _FakeSession(self._messages)


class FetchFrameBudgetTests(unittest.IsolatedAsyncioTestCase):
    def _capability(
        self, messages: tuple[MailFetchedMessage, ...]
    ) -> MailHostCapability:
        return MailHostCapability(
            _FakeBroker(messages),  # type: ignore[arg-type]
            InMemoryMailAccountRegistry((ACCOUNT,)),
        )

    async def _fetch(self, capability: MailHostCapability) -> dict[str, object]:
        return dict(
            await capability.handle(
                HOST_MAIL_FETCH,
                {"account_id": "nju", "folder": "INBOX", "start_uid": 0, "limit": 100},
                context=MailCapabilityContext(
                    extension_id="nju.smail", extension_version="0.1.0"
                ),
            )
        )

    async def test_batch_stays_inside_the_worker_frame_limit(self) -> None:
        messages = tuple(_message(uid, 300 * 1024) for uid in (1, 2, 3))
        capability = self._capability(messages)
        response = await self._fetch(capability)
        encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.assertLess(len(encoded), WORKER_FRAME_LIMIT)
        self.assertTrue(response["batch_limited"])
        views = response["messages"]
        assert isinstance(views, list)
        self.assertLess(len(views), len(messages))
        total_raw = sum(len(str(view["raw_base64"])) for view in views)
        self.assertLessEqual(total_raw, MAX_FETCH_RAW_BYTES * 4 // 3 + 8)
        # The message that did not fit is deferred, not silently dropped.
        returned = [view["uid"] for view in views]
        self.assertEqual(returned, list(range(1, len(returned) + 1)))
        self.assertTrue(views[-1]["truncated"])

    async def test_small_batch_is_returned_whole_without_limiting(self) -> None:
        messages = (_message(1, 1024), _message(2, 2048))
        response = await self._fetch(self._capability(messages))
        self.assertFalse(response["batch_limited"])
        views = response["messages"]
        assert isinstance(views, list)
        self.assertEqual([view["uid"] for view in views], [1, 2])
        self.assertFalse(views[0]["truncated"])
        self.assertFalse(views[1]["truncated"])

    async def test_single_oversize_message_is_truncated_and_still_fits(self) -> None:
        messages = (_message(1, MAX_FETCH_RAW_BYTES * 3),)
        response = await self._fetch(self._capability(messages))
        encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.assertLess(len(encoded), WORKER_FRAME_LIMIT)
        views = response["messages"]
        assert isinstance(views, list)
        self.assertEqual(len(views), 1)
        self.assertTrue(views[0]["truncated"])
        self.assertFalse(response["batch_limited"])


if __name__ == "__main__":
    unittest.main()
