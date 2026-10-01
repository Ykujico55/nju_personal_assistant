"""F08.5: reject deceptive destinations and never retry ambiguous delivery."""

from __future__ import annotations

import base64
import unittest

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from personal_assistant.domain import ConcurrentModificationError, ValidationError
from personal_assistant.infrastructure.memory import InMemorySecretStore
from personal_assistant.infrastructure.memory.push import InMemoryPushSubscriptionStore
from personal_assistant.infrastructure.push.service import PushService


def _encoded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class _Sender:
    def __init__(self) -> None:
        self.calls = 0
        self.endpoints: list[str] = []
        self.outcome = "unknown"

    async def send(self, **kwargs: object) -> str:
        self.calls += 1
        self.endpoints.append(str(kwargs["endpoint"]))
        return self.outcome

    async def aclose(self) -> None:
        pass


class WebPushServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.secrets = InMemorySecretStore()
        handle = await self.secrets.put(
            name="push", kind="push_vapid_private",
            value=_encoded((7).to_bytes(32, "big")),
        )
        self.public = _encoded(ec.derive_private_key(7, ec.SECP256R1())
                          .public_key().public_bytes(
                              Encoding.X962, PublicFormat.UncompressedPoint
                          ))
        self.sender = _Sender()
        self.store = InMemoryPushSubscriptionStore()
        self.service = PushService(
            store=self.store, secrets=self.secrets, sender=self.sender,
            owner_id="owner", public_key=self.public,
            secret_handle_id=handle.id,
            allowed_origins=("https://push.example.test",),
        )
        self.browser_key = _encoded(ec.derive_private_key(11, ec.SECP256R1())
                                    .public_key().public_bytes(
                                        Encoding.X962, PublicFormat.UncompressedPoint
                                    ))
        self.handle_id = handle.id

    async def test_deceptive_or_control_character_endpoint_is_rejected(self) -> None:
        for endpoint in (
            "https://push.example.test.evil.test/send",
            "https://push.example.test@127.0.0.1/send",
            "https://push.example.test/send\n",
            "https://push.example.test:444/send",
            "http://push.example.test/send",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValidationError):
                await self.service.subscribe(endpoint, self.browser_key, _encoded(b"a" * 16))
        self.assertEqual((), await self.store.list_active("owner"))

    async def test_ambiguous_push_is_not_retried_or_reported_as_accepted(self) -> None:
        await self.service.subscribe(
            "https://push.example.test/send/opaque", self.browser_key,
            _encoded(b"a" * 16),
        )
        report = await self.service.notify_pending(task_id="task_1", risk="R2")
        self.assertEqual((0, 0, 1), (report.accepted, report.expired, report.unknown))
        self.assertEqual(1, self.sender.calls)

    async def test_expired_push_revokes_server_record(self) -> None:
        self.sender.outcome = "expired"
        receipt = await self.service.subscribe(
            "https://push.example.test/send/expired", self.browser_key,
            _encoded(b"a" * 16),
        )
        report = await self.service.notify_pending(task_id="task_1", risk="R2")
        self.assertEqual((0, 1, 0), (report.accepted, report.expired, report.unknown))
        self.assertFalse(await self.service.status(receipt.id))
        self.assertEqual((), await self.store.list_active("owner"))

    async def test_removed_origin_never_receives_an_existing_subscription(self) -> None:
        record = await self.service.subscribe(
            "https://push.example.test/send/old", self.browser_key, _encoded(b"a" * 16)
        )
        sender = _Sender()
        sender.outcome = "accepted"
        changed = PushService(
            store=self.store, secrets=self.secrets, sender=sender,
            owner_id="owner", public_key=self.public,
            secret_handle_id=self.handle_id,
            allowed_origins=("https://other.example.test",),
        )
        report = await changed.notify_pending(task_id="task_1", risk="R2")
        self.assertEqual([], sender.endpoints)
        self.assertEqual(1, report.reconfigure_required)
        self.assertEqual(0, report.accepted)
        health = await changed.inspect(record.id)
        self.assertFalse(health.active)
        self.assertTrue(health.reconfigure_required)

    async def test_rotated_key_old_record_does_not_block_new_device(self) -> None:
        old = await self.service.subscribe(
            "https://push.example.test/send/old", self.browser_key, _encoded(b"a" * 16)
        )
        new_private = ec.derive_private_key(9, ec.SECP256R1())
        handle = await self.secrets.put(
            name="push-rotated", kind="push_vapid_private",
            value=_encoded((9).to_bytes(32, "big")),
        )
        new_public = _encoded(new_private.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        ))
        sender = _Sender()
        sender.outcome = "accepted"
        rotated = PushService(
            store=self.store, secrets=self.secrets, sender=sender,
            owner_id="owner", public_key=new_public,
            secret_handle_id=handle.id,
            allowed_origins=("https://push.example.test",),
        )
        fresh = await rotated.subscribe(
            "https://push.example.test/send/new", self.browser_key, _encoded(b"b" * 16)
        )
        report = await rotated.notify_pending(task_id="task_1", risk="R2")
        self.assertEqual(["https://push.example.test/send/new"], sender.endpoints)
        self.assertEqual((1, 1), (report.accepted, report.reconfigure_required))
        self.assertTrue((await rotated.inspect(fresh.id)).active)
        self.assertFalse((await rotated.inspect(old.id)).active)
        self.assertTrue((await rotated.inspect(old.id)).reconfigure_required)

    async def test_replay_uses_original_command_before_current_origin_policy(self) -> None:
        endpoint = "https://push.example.test/send/old"
        original = await self.service.subscribe(
            endpoint, self.browser_key, _encoded(b"a" * 16), key="subscribe-1"
        )
        changed = PushService(
            store=self.store, secrets=self.secrets, sender=_Sender(),
            owner_id="owner", public_key=self.public,
            secret_handle_id=self.handle_id,
            allowed_origins=("https://other.example.test",),
        )
        self.assertEqual(original, await changed.subscribe(
            endpoint, self.browser_key, _encoded(b"a" * 16), key="subscribe-1"
        ))
        with self.assertRaises(ConcurrentModificationError):
            await changed.subscribe(
                endpoint, self.browser_key, _encoded(b"b" * 16), key="subscribe-1"
            )
