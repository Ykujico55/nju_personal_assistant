"""F08.5 RFC vector and outbound Web Push safety checks."""

from __future__ import annotations

import base64
import json
import unittest
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from personal_assistant.infrastructure.push.transport import HttpWebPushSender, encrypt_aes128gcm


def decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class WebPushTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_rfc8291_encryption_vector(self) -> None:
        browser_public = decode(
            "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-"
            "JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
        )
        private = ec.derive_private_key(int.from_bytes(decode(
            "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
        ), "big"), ec.SECP256R1())
        encrypted = encrypt_aes128gcm(
            b"When I grow up, I want to be a watermelon",
            p256dh=browser_public,
            auth=decode("BTBZMqHH6r4Tts7J_aSIgg"),
            ephemeral_private=private,
            salt=decode("DGv6ra1nlYgDCS1FRnbzlw"),
        )
        self.assertEqual(
            decode(
                "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
                "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
                "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
            ),
            encrypted,
        )

    async def test_vapid_audience_no_redirect_and_no_sensitive_payload(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(307, headers={"Location": "https://other.test/steal"})
            return httpx.Response(201)

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        )
        sender = HttpWebPushSender(client=client)
        private = ec.derive_private_key(7, ec.SECP256R1())
        public = private.public_key().public_numbers()
        vapid_public = b"\x04" + public.x.to_bytes(32, "big") + public.y.to_bytes(32, "big")
        outcome = await sender.send(
            endpoint="https://push.example.test/send/opaque",
            p256dh=ec.derive_private_key(11, ec.SECP256R1()).public_key().public_bytes(
                encoding=Encoding.X962,
                format=PublicFormat.UncompressedPoint,
            ),
            auth=b"a" * 16,
            payload=json.dumps({"kind": "pending", "risk": "R2", "task_id": "task_1"}).encode(),
            private_key=private,
            public_key=base64.urlsafe_b64encode(vapid_public).decode().rstrip("="),
        )
        self.assertEqual("unknown", outcome)
        self.assertEqual(1, len(requests))
        request = requests[0]
        self.assertEqual("https://push.example.test", f"{request.url.scheme}://{request.url.host}")
        self.assertEqual("aes128gcm", request.headers["Content-Encoding"])
        self.assertNotIn(b"task_1", request.content)
        authorization = request.headers["Authorization"]
        self.assertTrue(authorization.startswith("vapid t="))
        token = authorization.split("t=", 1)[1].split(",", 1)[0]
        claims = jwt.decode(token, private.public_key(), algorithms=["ES256"], audience="https://push.example.test")
        self.assertEqual("https://push.example.test", claims["aud"])
        self.assertEqual("push.example.test", urlsplit(str(request.url)).hostname)
        await sender.aclose()

    async def test_http_410_is_reported_as_expired_once(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(410)

        sender = HttpWebPushSender(client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ))
        private = ec.derive_private_key(7, ec.SECP256R1())
        browser = ec.derive_private_key(11, ec.SECP256R1()).public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
        outcome = await sender.send(
            endpoint="https://push.example.test/send/expired", p256dh=browser,
            auth=b"a" * 16, payload=b'{"kind":"pending"}', private_key=private,
            public_key="test-public-key",
        )
        self.assertEqual("expired", outcome)
        self.assertEqual(1, len(requests))
        await sender.aclose()
