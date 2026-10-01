"""F08.5: device subscriptions are scoped, durable and never echoed as secrets."""

from __future__ import annotations

import asyncio
import base64
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from personal_assistant.app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.infrastructure.memory import InMemorySecretStore
from personal_assistant.settings import Settings


def encoded(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class PwaPushApiTests(unittest.TestCase):
    def test_subscribe_replay_status_revoke_and_reconnect_without_echoing_secrets(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            baseline = Settings.from_env()
        secrets = InMemorySecretStore()
        private = ec.derive_private_key(7, ec.SECP256R1())
        handle = asyncio.run(secrets.put(
            name="vapid", kind="push_vapid_private", value=encoded((7).to_bytes(32, "big"))
        ))
        public = encoded(private.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        ))
        settings = replace(
            baseline,
            push_vapid_public_key=public,
            push_vapid_secret_handle=handle.id,
            push_allowed_origins=("https://push.example.test",),
        )
        endpoint = "https://push.example.test/send/opaque-token"
        browser_key = encoded(ec.derive_private_key(11, ec.SECP256R1()).public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        ))
        subscription = {"endpoint": endpoint, "keys": {
            "p256dh": browser_key, "auth": encoded(b"a" * 16),
        }}
        headers = {"Idempotency-Key": "push-subscribe-1"}
        container = build_container(settings, secret_store=secrets)
        with TestClient(create_app(settings=settings, container=container)) as client:
            config = client.get("/api/v1/push/config")
            self.assertEqual(200, config.status_code)
            self.assertEqual({"enabled": True, "public_key": public}, config.json())
            self.assertEqual("no-store", config.headers["cache-control"])
            created = client.post(
                "/api/v1/push/subscriptions", json=subscription, headers=headers
            )
            self.assertEqual(201, created.status_code, created.text)
            receipt = created.json()
            self.assertNotIn(endpoint, created.text)
            self.assertNotIn(browser_key, created.text)
            self.assertNotIn(subscription["keys"]["auth"], created.text)
            self.assertEqual("no-store", created.headers["cache-control"])
            repeated = client.post(
                "/api/v1/push/subscriptions", json=subscription, headers=headers
            )
            self.assertEqual(receipt, repeated.json())
            before = asyncio.run(container.push._store.get("owner", receipt["id"]))
            changed_auth = {"endpoint": endpoint, "keys": {
                "p256dh": browser_key, "auth": encoded(b"b" * 16),
            }}
            conflict = client.post(
                "/api/v1/push/subscriptions", json=changed_auth, headers=headers
            )
            self.assertEqual(409, conflict.status_code, conflict.text)
            after = asyncio.run(container.push._store.get("owner", receipt["id"]))
            self.assertEqual(before, after)
            active = client.get(f"/api/v1/push/subscriptions/{receipt['id']}")
            self.assertEqual(
                {"id": receipt["id"], "active": True, "reconfigure_required": False},
                active.json(),
            )
            removed = client.delete(
                f"/api/v1/push/subscriptions/{receipt['id']}",
                headers={"Idempotency-Key": "push-revoke-1"},
            )
            self.assertEqual(204, removed.status_code, removed.text)
            self.assertEqual("no-store", removed.headers["cache-control"])
            self.assertEqual(
                {"id": receipt["id"], "active": False, "reconfigure_required": False},
                client.get(f"/api/v1/push/subscriptions/{receipt['id']}").json(),
            )
            replay_after_revoke = client.post(
                "/api/v1/push/subscriptions", json=subscription, headers=headers
            )
            self.assertEqual(201, replay_after_revoke.status_code)
            self.assertEqual(receipt, replay_after_revoke.json())
            self.assertFalse(client.get(
                f"/api/v1/push/subscriptions/{receipt['id']}"
            ).json()["active"])
            self.assertEqual(
                204,
                client.delete(
                    f"/api/v1/push/subscriptions/{receipt['id']}",
                    headers={"Idempotency-Key": "push-revoke-1"},
                ).status_code,
            )

    def test_unapproved_or_local_push_endpoint_is_rejected_before_storage(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        container = build_container(settings)
        with TestClient(create_app(settings=settings, container=container)) as client:
            response = client.post(
                "/api/v1/push/subscriptions",
                json={"endpoint": "http://127.0.0.1:8001/admin/v1", "keys": {
                    "p256dh": "invalid", "auth": "invalid",
                }},
                headers={"Idempotency-Key": "push-ssrf-1"},
            )
            self.assertIn(response.status_code, (400, 422, 503), response.text)
