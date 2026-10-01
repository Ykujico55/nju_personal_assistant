"""RFC 8291 payload encryption and RFC 8292 VAPID single-attempt delivery."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def encrypt_aes128gcm(
    plaintext: bytes, *, p256dh: bytes, auth: bytes,
    ephemeral_private: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """Encrypt exactly one Web Push record, following RFC 8291 section 3.4."""

    if len(auth) != 16 or len(p256dh) != 65 or p256dh[0] != 4:
        raise ValueError("invalid browser encryption material")
    if len(plaintext) > 3992:
        raise ValueError("Web Push payload exceeds one record")
    private = ephemeral_private or ec.generate_private_key(ec.SECP256R1())
    salt_bytes = salt if salt is not None else os.urandom(16)
    if len(salt_bytes) != 16:
        raise ValueError("Web Push salt must be 16 bytes")
    server_public = private.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    browser_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
    ecdh_secret = private.exchange(ec.ECDH(), browser_public)
    key_prk = hmac.new(auth, ecdh_secret, hashlib.sha256).digest()
    ikm = hmac.new(
        key_prk, b"WebPush: info\x00" + p256dh + server_public + b"\x01",
        hashlib.sha256,
    ).digest()
    prk = hmac.new(salt_bytes, ikm, hashlib.sha256).digest()
    cek = hmac.new(
        prk, b"Content-Encoding: aes128gcm\x00\x01", hashlib.sha256
    ).digest()[:16]
    nonce = hmac.new(
        prk, b"Content-Encoding: nonce\x00\x01", hashlib.sha256
    ).digest()[:12]
    encrypted = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt_bytes + (4096).to_bytes(4, "big") + bytes([65]) + server_public + encrypted


class HttpWebPushSender:
    """One POST per hint; redirects, ambient proxies and retries are disabled."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(5.0), follow_redirects=False, trust_env=False
        )

    async def send(
        self, *, endpoint: str, p256dh: bytes, auth: bytes, payload: bytes,
        private_key: ec.EllipticCurvePrivateKey, public_key: str,
    ) -> str:
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ""
        origin = f"https://{host}" if parsed.port in (None, 443) else f"https://{host}:{parsed.port}"
        token = jwt.encode(
            {"aud": origin, "exp": int(time.time()) + 3600},
            private_key, algorithm="ES256",
        )
        body = encrypt_aes128gcm(payload, p256dh=p256dh, auth=auth)
        try:
            response = await self._client.post(
                endpoint,
                content=body,
                headers={
                    "Authorization": f"vapid t={token}, k={public_key}",
                    "Content-Encoding": "aes128gcm",
                    "Content-Type": "application/octet-stream",
                    "TTL": "60",
                },
                follow_redirects=False,
            )
        except httpx.HTTPError:
            return "unknown"
        if response.status_code in (201, 202):
            return "accepted"
        if response.status_code in (404, 410):
            return "expired"
        return "unknown"

    async def aclose(self) -> None:
        await self._client.aclose()
