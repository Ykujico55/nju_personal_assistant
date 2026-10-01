"""Web Push control plane; subscription capabilities are sealed before storage."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from typing import Protocol
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from personal_assistant.core.notifications.push import (
    PushDeliveryReport,
    PushSubscriptionHealth,
    PushSubscriptionReceipt,
    PushSubscriptionRecord,
    PushSubscriptionStore,
    PushUnavailableError,
)
from personal_assistant.core.secrets import SecretHandle, SecretStorePort
from personal_assistant.domain import ValidationError, utc_now

_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SUBSCRIPTION_ID = re.compile(r"^[0-9a-f]{64}$")
_RISK = frozenset({"R0", "R1", "R2", "R3"})
_MAX_ENDPOINT_BYTES = 2048


class PushSender(Protocol):
    async def send(
        self, *, endpoint: str, p256dh: bytes, auth: bytes, payload: bytes,
        private_key: ec.EllipticCurvePrivateKey, public_key: str,
    ) -> str: ...

    async def aclose(self) -> None: ...


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str, size: int, name: str) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise ValidationError(f"{name} must be unpadded base64url")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError(f"{name} is invalid") from exc
    if len(decoded) != size or _encode(decoded) != value:
        raise ValidationError(f"{name} has an invalid length or encoding")
    return decoded


class PushService:
    def __init__(
        self, *, store: PushSubscriptionStore, secrets: SecretStorePort,
        sender: PushSender, owner_id: str, public_key: str | None,
        secret_handle_id: str | None, allowed_origins: tuple[str, ...],
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._sender = sender
        self._owner_id = owner_id
        self.public_key = public_key
        self._secret_handle_id = secret_handle_id
        self._allowed_origins = frozenset(allowed_origins)

    async def configured_public_key(self) -> str | None:
        try:
            await self._private_key()
        except PushUnavailableError:
            return None
        return self.public_key

    async def _private_key(self) -> ec.EllipticCurvePrivateKey:
        if not self.public_key or not self._secret_handle_id or not self._allowed_origins:
            raise PushUnavailableError("Web Push is not configured")
        try:
            raw = await self._secrets.resolve_for_broker(
                SecretHandle(self._secret_handle_id, "push_vapid_private"),
                purpose="web_push",
            )
            scalar = _decode(raw, 32, "VAPID private key")
            private = ec.derive_private_key(int.from_bytes(scalar, "big"), ec.SECP256R1())
            public = private.public_key().public_bytes(
                Encoding.X962, PublicFormat.UncompressedPoint
            )
            if _encode(public) != self.public_key:
                raise ValueError("VAPID key pair mismatch")
            return private
        except Exception as exc:
            raise PushUnavailableError("Web Push host credential is unavailable") from exc

    def _endpoint(self, endpoint: str) -> str:
        if (not isinstance(endpoint, str)
                or len(endpoint.encode("utf-8")) > _MAX_ENDPOINT_BYTES
                or any(ord(character) <= 32 or ord(character) == 127
                       for character in endpoint)):
            raise ValidationError("push endpoint is invalid")
        try:
            parsed = urlsplit(endpoint)
            port = parsed.port
        except ValueError as exc:
            raise ValidationError("push endpoint is invalid") from exc
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or port not in (None, 443)
            or not parsed.path or parsed.path == "/"
        ):
            raise ValidationError("push endpoint must be an allowed HTTPS URL")
        try:
            host = parsed.hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValidationError("push endpoint host is invalid") from exc
        if f"https://{host}" not in self._allowed_origins:
            raise ValidationError("push endpoint origin is not allowed")
        return endpoint

    async def subscribe(
        self, endpoint: str, p256dh: str, auth: str, *, key: str | None = None
    ) -> PushSubscriptionReceipt:
        request_sha256 = hashlib.sha256(json.dumps(
            {"endpoint": endpoint, "p256dh": p256dh, "auth": auth},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        if key is not None:
            replayed = await self._store.replay(self._owner_id, key, request_sha256)
            if replayed is not None:
                return replayed
        private = await self._private_key()
        target = self._endpoint(endpoint)
        browser_public = _decode(p256dh, 65, "p256dh")
        if browser_public[0] != 4:
            raise ValidationError("p256dh must be an uncompressed P-256 point")
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), browser_public)
        except ValueError as exc:
            raise ValidationError("p256dh is not a P-256 point") from exc
        _decode(auth, 16, "auth")
        identifier = hashlib.sha256(target.encode("utf-8")).hexdigest()
        material = json.dumps(
            {"endpoint": target, "p256dh": p256dh, "auth": auth},
            separators=(",", ":"),
        ).encode("utf-8")
        sealed = self._seal(private, identifier, material)
        record = PushSubscriptionRecord(
            id=identifier, owner_id=self._owner_id, sealed=sealed, created_at=utc_now()
        )
        if key is not None:
            return await self._store.put_command(record, key, request_sha256)
        saved = await self._store.put(record)
        return PushSubscriptionReceipt(id=saved.id, created_at=saved.created_at)

    async def status(self, subscription_id: str) -> bool:
        return (await self.inspect(subscription_id)).active

    async def inspect(self, subscription_id: str) -> PushSubscriptionHealth:
        self._check_id(subscription_id)
        record = await self._store.get(self._owner_id, subscription_id)
        if record is None:
            return PushSubscriptionHealth(active=False, reconfigure_required=False)
        try:
            private = await self._private_key()
            self._material(private, record)
        except (PushUnavailableError, InvalidTag, ValidationError, ValueError,
                KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return PushSubscriptionHealth(active=False, reconfigure_required=True)
        return PushSubscriptionHealth(active=True, reconfigure_required=False)

    async def revoke(self, subscription_id: str) -> None:
        self._check_id(subscription_id)
        await self._store.revoke(self._owner_id, subscription_id)

    async def notify_pending(self, *, task_id: str, risk: str) -> PushDeliveryReport:
        """Send one minimal hint to each device; never retry an ambiguous POST."""

        if _TASK_ID.fullmatch(task_id) is None or risk not in _RISK:
            raise ValidationError("notification metadata is invalid")
        private = await self._private_key()
        payload = json.dumps(
            {"kind": "pending", "risk": risk, "task_id": task_id},
            separators=(",", ":"),
        ).encode("utf-8")
        accepted = expired = unknown = reconfigure_required = 0
        for record in await self._store.list_active(self._owner_id):
            try:
                endpoint, p256dh, auth = self._material(private, record)
            except (InvalidTag, ValidationError, ValueError, KeyError, TypeError,
                    UnicodeDecodeError, json.JSONDecodeError):
                reconfigure_required += 1
                continue
            try:
                outcome = await self._sender.send(
                    endpoint=endpoint,
                    p256dh=p256dh,
                    auth=auth,
                    payload=payload,
                    private_key=private,
                    public_key=self.public_key or "",
                )
            except Exception:
                # A push service may have accepted the POST. Report UNKNOWN,
                # without an automatic retry or a false success response.
                unknown += 1
                continue
            if outcome == "accepted":
                accepted += 1
            elif outcome == "expired":
                expired += 1
                await self._store.revoke(self._owner_id, record.id)
            else:
                unknown += 1
        return PushDeliveryReport(accepted, expired, unknown, reconfigure_required)

    def _material(
        self, private: ec.EllipticCurvePrivateKey, record: PushSubscriptionRecord
    ) -> tuple[str, bytes, bytes]:
        material = json.loads(self._open(private, record))
        if not isinstance(material, dict):
            raise ValidationError("stored push subscription is invalid")
        endpoint = self._endpoint(material["endpoint"])
        if hashlib.sha256(endpoint.encode("utf-8")).hexdigest() != record.id:
            raise ValidationError("stored push endpoint does not match its id")
        p256dh = _decode(material["p256dh"], 65, "p256dh")
        if p256dh[0] != 4:
            raise ValidationError("stored p256dh is not an uncompressed P-256 point")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
        return endpoint, p256dh, _decode(material["auth"], 16, "auth")

    async def aclose(self) -> None:
        await self._sender.aclose()

    def _seal(self, private: ec.EllipticCurvePrivateKey, identifier: str, data: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self._storage_key(private)).encrypt(
            nonce, data, f"{self._owner_id}:{identifier}".encode()
        )

    def _open(self, private: ec.EllipticCurvePrivateKey, record: PushSubscriptionRecord) -> bytes:
        return AESGCM(self._storage_key(private)).decrypt(
            record.sealed[:12], record.sealed[12:],
            f"{record.owner_id}:{record.id}".encode(),
        )

    @staticmethod
    def _storage_key(private: ec.EllipticCurvePrivateKey) -> bytes:
        return hashlib.sha256(
            b"personal-assistant:push-subscriptions:v1\0"
            + private.private_numbers().private_value.to_bytes(32, "big")
        ).digest()

    @staticmethod
    def _check_id(identifier: str) -> None:
        if _SUBSCRIPTION_ID.fullmatch(identifier) is None:
            raise ValidationError("push subscription id is invalid")
