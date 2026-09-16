"""F03 unit tests for Cloudflare Access JWT verification and JWKS handling.

All keys, tokens and JWKS payloads are generated at runtime.  No real
Cloudflare endpoint, account data or credential is used anywhere.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
import unittest
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from personal_assistant.infrastructure.auth import (
    AccessIdentity,
    AccessTokenError,
    AccessTokenRejectedError,
    AccessTokenUnavailableError,
    CloudflareAccessTokenVerifier,
    CloudflareJwksProvider,
    UnknownSigningKeyError,
    cloudflare_access_verifier_from_settings,
)
from personal_assistant.settings import ConfigurationError, Settings

ISSUER = "https://team.cloudflareaccess.com"
CERTS_URL = f"{ISSUER}/cdn-cgi/access/certs"
AUDIENCE = "application-audience-tag"
KID = "test-signing-key-1"


class FakeClock:
    def __init__(self, now: float | None = None) -> None:
        self.now = time.time() if now is None else now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CountingTransport(httpx.MockTransport):
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []

        def wrapped(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        super().__init__(wrapped)


def generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk_for(private_key: rsa.RSAPrivateKey, kid: str) -> dict[str, object]:
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})
    return jwk


def encode_token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = KID,
    issuer: str = ISSUER,
    audience: object = AUDIENCE,
    subject: object = "subject-123",
    email: object | None = None,
    expires_in: float = 300,
    not_before: float | None = None,
    issued_at: float | None = None,
    now: float | None = None,
    algorithm: str = "RS256",
    key: Any | None = None,
) -> str:
    issued = time.time() if now is None else now
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "exp": int(issued + expires_in),
    }
    if email is not None:
        claims["email"] = email
    if not_before is not None:
        claims["nbf"] = int(not_before)
    if issued_at is not None:
        claims["iat"] = int(issued_at)
    signing_key = None if algorithm == "none" else (private_key if key is None else key)
    return jwt.encode(claims, signing_key, algorithm=algorithm, headers={"kid": kid})


class AccessFixture:
    def __init__(
        self,
        *,
        cache_ttl_seconds: float = 300.0,
        min_refresh_interval_seconds: float = 30.0,
        clock: FakeClock | None = None,
    ) -> None:
        self.clock = clock or FakeClock()
        self.private_key = generate_key()
        self.kid = KID
        self.payload: object = {"keys": [jwk_for(self.private_key, KID)]}
        self.status_code = 200
        self.raw_body: bytes | None = None
        self.raise_error: Exception | None = None
        self.delay_seconds = 0.0
        self.transport = CountingTransport(self._handle)
        self.client = httpx.AsyncClient(transport=self.transport)
        self.provider = CloudflareJwksProvider(
            certs_url=CERTS_URL,
            http_client=self.client,
            clock=self.clock,
            cache_ttl_seconds=cache_ttl_seconds,
            min_refresh_interval_seconds=min_refresh_interval_seconds,
        )
        self.verifier = CloudflareAccessTokenVerifier(
            provider=self.provider,
            issuer=ISSUER,
            audience=AUDIENCE,
            clock=self.clock,
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.raise_error is not None:
            raise self.raise_error
        if self.raw_body is not None:
            return httpx.Response(
                self.status_code,
                content=self.raw_body,
                headers={"content-type": "application/json"},
            )
        if self.payload is None:
            return httpx.Response(self.status_code, content=b"")
        return httpx.Response(self.status_code, json=self.payload)

    def set_keys(self, *pairs: tuple[str, rsa.RSAPrivateKey]) -> None:
        self.payload = {"keys": [jwk_for(key, kid) for kid, key in pairs]}

    def token(self, **kwargs: object) -> str:
        kwargs.setdefault("now", self.clock())
        return encode_token(self.private_key, **kwargs)  # type: ignore[arg-type]

    async def aclose(self) -> None:
        await self.client.aclose()


class VerifierClaimTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fixture = AccessFixture()

    async def asyncTearDown(self) -> None:
        await self.fixture.aclose()

    async def test_valid_token_maps_signed_subject_and_email(self) -> None:
        token = self.fixture.token(email="owner@example.test")
        identity = await self.fixture.verifier.verify(token)
        self.assertEqual(
            AccessIdentity(subject="subject-123", email="owner@example.test"), identity
        )

    async def test_valid_token_without_email(self) -> None:
        identity = await self.fixture.verifier.verify(self.fixture.token())
        self.assertEqual("subject-123", identity.subject)
        self.assertIsNone(identity.email)

    async def test_audience_array_containing_target_is_accepted(self) -> None:
        token = self.fixture.token(audience=["other-app", AUDIENCE])
        identity = await self.fixture.verifier.verify(token)
        self.assertEqual("subject-123", identity.subject)

    async def test_audience_array_without_target_is_rejected(self) -> None:
        token = self.fixture.token(audience=["other-app"])
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_wrong_audience_is_rejected(self) -> None:
        token = self.fixture.token(audience="different-audience")
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_wrong_issuer_is_rejected(self) -> None:
        token = self.fixture.token(issuer="https://evil.cloudflareaccess.com")
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_missing_expiry_is_rejected(self) -> None:
        token = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "subject-123"},
            self.fixture.private_key,
            algorithm="RS256",
            headers={"kid": KID},
        )
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_expired_token_is_rejected(self) -> None:
        token = self.fixture.token(expires_in=-600)
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_token_expiring_within_leeway_is_accepted(self) -> None:
        token = self.fixture.token(expires_in=-10)
        identity = await self.fixture.verifier.verify(token)
        self.assertEqual("subject-123", identity.subject)

    async def test_future_not_before_is_rejected(self) -> None:
        token = self.fixture.token(not_before=self.fixture.clock() + 3600)
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_past_not_before_is_accepted(self) -> None:
        token = self.fixture.token(not_before=self.fixture.clock() - 60)
        identity = await self.fixture.verifier.verify(token)
        self.assertEqual("subject-123", identity.subject)

    async def test_future_issued_at_is_rejected(self) -> None:
        token = self.fixture.token(issued_at=self.fixture.clock() + 3600)
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_inconsistent_time_claims_are_rejected(self) -> None:
        token = self.fixture.token(
            not_before=self.fixture.clock() + 100, expires_in=50
        )
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_non_numeric_expiry_is_rejected(self) -> None:
        token = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "subject-123", "exp": "soon"},
            self.fixture.private_key,
            algorithm="RS256",
            headers={"kid": KID},
        )
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_boolean_expiry_is_rejected(self) -> None:
        token = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "subject-123", "exp": True},
            self.fixture.private_key,
            algorithm="RS256",
            headers={"kid": KID},
        )
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_missing_subject_is_rejected(self) -> None:
        token = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "exp": int(self.fixture.clock() + 300)},
            self.fixture.private_key,
            algorithm="RS256",
            headers={"kid": KID},
        )
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_empty_subject_is_rejected(self) -> None:
        token = self.fixture.token(subject="   ")
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)

    async def test_non_string_email_is_rejected(self) -> None:
        token = self.fixture.token(email={"value": "owner@example.test"})
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)


class VerifierAlgorithmTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fixture = AccessFixture()

    async def asyncTearDown(self) -> None:
        await self.fixture.aclose()

    async def test_malformed_token_is_rejected(self) -> None:
        for token in ("", "not-a-jwt", "a.b", "a.b.c.d", "eyJhbGciOiJSUzI1NiJ9!!!.!!!"):
            with self.subTest(token=token), self.assertRaises(AccessTokenRejectedError):
                await self.fixture.verifier.verify(token)

    async def test_alg_none_is_rejected(self) -> None:
        token = encode_token(
            self.fixture.private_key,
            key=None,
            algorithm="none",
            now=self.fixture.clock(),
        )
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)
        self.assertEqual([], self.fixture.transport.requests)

    async def test_symmetric_algorithm_confusion_is_rejected(self) -> None:
        token = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "subject-123",
                "exp": int(self.fixture.clock() + 300),
            },
            "shared-secret",
            algorithm="HS256",
            headers={"kid": KID},
        )
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)
        self.assertEqual([], self.fixture.transport.requests)

    async def test_missing_kid_is_rejected_without_network(self) -> None:
        token = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "subject-123",
                "exp": int(self.fixture.clock() + 300),
            },
            self.fixture.private_key,
            algorithm="RS256",
        )
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)
        self.assertEqual([], self.fixture.transport.requests)

    async def test_unknown_kid_refreshes_once_then_rejects(self) -> None:
        token = self.fixture.token(kid="unknown-key")
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)
        self.assertEqual(1, len(self.fixture.transport.requests))

    async def test_throttled_unknown_kid_is_temporarily_unavailable(self) -> None:
        first = self.fixture.token(kid="unknown-key")
        second = self.fixture.token(kid="other-unknown-key")
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(first)
        # The second kid was never checked because of the refresh throttle, so
        # it must be a retryable availability failure, not a permanent 401.
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(second)
        self.assertEqual(1, len(self.fixture.transport.requests))

    async def test_wrong_signature_is_rejected(self) -> None:
        rogue = generate_key()
        token = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "subject-123",
                "exp": int(self.fixture.clock() + 300),
            },
            rogue,
            algorithm="RS256",
            headers={"kid": KID},
        )
        with self.assertRaises(AccessTokenRejectedError):
            await self.fixture.verifier.verify(token)


class JwksProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fixture = AccessFixture()

    async def asyncTearDown(self) -> None:
        await self.fixture.aclose()

    async def test_first_fetch_hits_official_certs_url(self) -> None:
        await self.fixture.verifier.verify(self.fixture.token())
        self.assertEqual(1, len(self.fixture.transport.requests))
        self.assertEqual(CERTS_URL, str(self.fixture.transport.requests[0].url))
        self.assertEqual("GET", self.fixture.transport.requests[0].method)

    async def test_cached_key_is_reused_without_network(self) -> None:
        await self.fixture.verifier.verify(self.fixture.token())
        await self.fixture.verifier.verify(self.fixture.token())
        await self.fixture.verifier.verify(self.fixture.token())
        self.assertEqual(1, len(self.fixture.transport.requests))

    async def test_expired_cache_triggers_refresh(self) -> None:
        await self.fixture.verifier.verify(self.fixture.token())
        self.fixture.clock.advance(301)
        await self.fixture.verifier.verify(self.fixture.token())
        self.assertEqual(2, len(self.fixture.transport.requests))

    async def test_unknown_kid_triggers_one_controlled_refresh(self) -> None:
        await self.fixture.verifier.verify(self.fixture.token())
        rotated_key = generate_key()
        self.fixture.set_keys((KID, self.fixture.private_key), ("rotated-key", rotated_key))
        rotated = encode_token(
            rotated_key,
            kid="rotated-key",
            now=self.fixture.clock(),
        )
        identity = await self.fixture.verifier.verify(rotated)
        self.assertEqual("subject-123", identity.subject)
        self.assertEqual(2, len(self.fixture.transport.requests))

    async def test_concurrent_refresh_is_coalesced(self) -> None:
        self.fixture.delay_seconds = 0.02
        token = self.fixture.token()
        results = await asyncio.gather(
            *(self.fixture.verifier.verify(token) for _ in range(10))
        )
        self.assertEqual(10, len(results))
        self.assertEqual(1, len(self.fixture.transport.requests))

    async def test_concurrent_unknown_kid_refresh_is_coalesced(self) -> None:
        self.fixture.delay_seconds = 0.02
        token = self.fixture.token(kid="unknown-key")
        outcomes = await asyncio.gather(
            *(self.fixture.verifier.verify(token) for _ in range(10)),
            return_exceptions=True,
        )
        # The caller that refreshed learns the kid is absent (401); callers
        # merged into that refresh may only learn that no refresh was needed.
        self.assertTrue(
            all(isinstance(item, AccessTokenError) for item in outcomes)
        )
        self.assertEqual(1, len(self.fixture.transport.requests))

    async def test_rotated_key_during_throttle_window_is_unavailable_then_recovers(
        self,
    ) -> None:
        fixture = self.fixture
        # An unknown kid triggers one refresh that does not contain it (401).
        with self.assertRaises(AccessTokenRejectedError):
            await fixture.verifier.verify(fixture.token(kid="attacker-key"))
        self.assertEqual(1, len(fixture.transport.requests))
        # A legitimate rotation appears immediately after; the throttle blocks
        # inspection, so the result must be a retryable 503, not a 401.
        rotated_key = generate_key()
        fixture.set_keys((KID, fixture.private_key), ("rotated-key", rotated_key))
        rotated = encode_token(rotated_key, kid="rotated-key", now=fixture.clock())
        with self.assertRaises(AccessTokenUnavailableError):
            await fixture.verifier.verify(rotated)
        self.assertEqual(1, len(fixture.transport.requests))
        # The previously cached key keeps working without any network access.
        identity = await fixture.verifier.verify(fixture.token())
        self.assertEqual("subject-123", identity.subject)
        self.assertEqual(1, len(fixture.transport.requests))
        # After the throttle window the next request refreshes and succeeds.
        fixture.clock.advance(31)
        identity = await fixture.verifier.verify(rotated)
        self.assertEqual("subject-123", identity.subject)
        self.assertEqual(2, len(fixture.transport.requests))

    async def test_stale_cache_is_not_used_when_refresh_fails(self) -> None:
        await self.fixture.verifier.verify(self.fixture.token())
        self.fixture.clock.advance(301)
        self.fixture.raise_error = httpx.ConnectError("connection refused")
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_network_error_is_unavailable(self) -> None:
        self.fixture.raise_error = httpx.ConnectError("connection refused")
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_timeout_is_unavailable(self) -> None:
        self.fixture.raise_error = httpx.ReadTimeout("timed out")
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_non_2xx_is_unavailable(self) -> None:
        self.fixture.status_code = 500
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_non_json_body_is_unavailable(self) -> None:
        self.fixture.raw_body = b"<html>not json</html>"
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_oversized_body_is_unavailable(self) -> None:
        self.fixture.raw_body = b"{" + b"a" * (128 * 1024) + b"}"
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_duplicate_kid_is_unavailable(self) -> None:
        key = generate_key()
        jwk = jwk_for(key, KID)
        self.fixture.payload = {"keys": [jwk, jwk]}
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_wrong_key_type_is_unavailable(self) -> None:
        jwk = jwk_for(self.fixture.private_key, KID)
        jwk["kty"] = "EC"
        self.fixture.payload = {"keys": [jwk]}
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_non_signing_use_is_unavailable(self) -> None:
        jwk = jwk_for(self.fixture.private_key, KID)
        jwk["use"] = "enc"
        self.fixture.payload = {"keys": [jwk]}
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_unexpected_algorithm_is_unavailable(self) -> None:
        jwk = jwk_for(self.fixture.private_key, KID)
        jwk["alg"] = "RS512"
        self.fixture.payload = {"keys": [jwk]}
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_empty_keys_is_unavailable(self) -> None:
        self.fixture.payload = {"keys": []}
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_malformed_jwks_shape_is_unavailable(self) -> None:
        self.fixture.payload = ["keys"]
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_malformed_key_entry_is_unavailable(self) -> None:
        self.fixture.payload = {"keys": ["not-a-dict"]}
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())

    async def test_failed_refresh_is_throttled_for_sequential_requests(self) -> None:
        self.fixture.raise_error = httpx.ConnectError("connection refused")
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())
        self.assertEqual(1, len(self.fixture.transport.requests))

    async def test_recovery_after_failed_refresh(self) -> None:
        self.fixture.raise_error = httpx.ConnectError("connection refused")
        with self.assertRaises(AccessTokenUnavailableError):
            await self.fixture.verifier.verify(self.fixture.token())
        self.fixture.raise_error = None
        self.fixture.clock.advance(31)
        identity = await self.fixture.verifier.verify(self.fixture.token())
        self.assertEqual("subject-123", identity.subject)

    async def test_unknown_key_error_type_is_exposed_for_direct_provider_use(self) -> None:
        with self.assertRaises(UnknownSigningKeyError):
            await self.fixture.provider.public_key("missing-kid")


class JwksRequestSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_injected_client_cannot_follow_redirects(self) -> None:
        key = generate_key()
        jwk = jwk_for(key, KID)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.host == "team.cloudflareaccess.com":
                return httpx.Response(
                    302,
                    headers={"Location": "https://attacker.example/cdn-cgi/access/certs"},
                )
            return httpx.Response(200, json={"keys": [jwk]})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            provider = CloudflareJwksProvider(
                certs_url=CERTS_URL, http_client=client, clock=FakeClock()
            )
            with self.assertRaises(AccessTokenUnavailableError):
                await provider.public_key(KID)
            self.assertEqual(1, len(requests))
            self.assertEqual("team.cloudflareaccess.com", requests[0].url.host)

    async def test_slow_drip_response_is_bounded_by_total_deadline(self) -> None:
        class SlowDripTransport(httpx.AsyncBaseTransport):
            def __init__(self) -> None:
                self.requests: list[httpx.Request] = []

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                self.requests.append(request)

                async def chunks() -> AsyncIterator[bytes]:
                    yield b'{"keys": ['
                    while True:
                        await asyncio.sleep(0.05)
                        yield b" "

                return httpx.Response(200, content=chunks())

        transport = SlowDripTransport()
        async with httpx.AsyncClient(transport=transport) as client:
            provider = CloudflareJwksProvider(
                certs_url=CERTS_URL,
                http_client=client,
                clock=FakeClock(),
                fetch_timeout_seconds=0.1,
            )
            started = time.perf_counter()
            with self.assertRaises(AccessTokenUnavailableError):
                await provider.public_key(KID)
            elapsed = time.perf_counter() - started
        self.assertEqual(1, len(transport.requests))
        self.assertLess(elapsed, 2.0)


class JwksCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_refresh_lets_a_waiter_take_over(self) -> None:
        clock = FakeClock()
        key = generate_key()
        jwk = jwk_for(key, KID)
        started = asyncio.Event()
        calls = 0

        class GatedTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                del request
                nonlocal calls
                calls += 1
                if calls == 1:
                    started.set()
                    await asyncio.Event().wait()
                return httpx.Response(200, json={"keys": [jwk]})

        async with httpx.AsyncClient(transport=GatedTransport()) as client:
            provider = CloudflareJwksProvider(
                certs_url=CERTS_URL, http_client=client, clock=clock
            )
            verifier = CloudflareAccessTokenVerifier(
                provider=provider, issuer=ISSUER, audience=AUDIENCE, clock=clock
            )
            token = encode_token(key, now=clock())
            first = asyncio.create_task(verifier.verify(token))
            await asyncio.wait_for(started.wait(), timeout=1)
            second = asyncio.create_task(verifier.verify(token))
            await asyncio.sleep(0.05)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            identity = await asyncio.wait_for(second, timeout=2)
        self.assertEqual("subject-123", identity.subject)
        self.assertEqual(2, calls)


class VerifierFactoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_factory_uses_normalized_team_domain(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PA_TRUST_CLOUDFLARE_ACCESS": "true",
                "PA_CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
                "PA_CF_ACCESS_AUD": AUDIENCE,
                "PA_PUBLIC_ORIGIN": "https://assistant.example.test",
            },
            clear=True,
        ):
            base = Settings.from_env()
        settings = dataclasses.replace(
            base, cf_access_team_domain=" TEAM.CloudflareAccess.com "
        )
        self.assertEqual(
            "https://team.cloudflareaccess.com", settings.cf_access_team_domain
        )
        key = generate_key()
        jwk = jwk_for(key, KID)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"keys": [jwk]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            verifier = cloudflare_access_verifier_from_settings(settings, http_client=client)
            identity = await verifier.verify(encode_token(key, now=time.time()))
        self.assertEqual("subject-123", identity.subject)
        self.assertEqual(CERTS_URL, str(requests[0].url))

    async def test_factory_defensively_normalizes_bypassed_settings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PA_TRUST_CLOUDFLARE_ACCESS": "true",
                "PA_CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
                "PA_CF_ACCESS_AUD": AUDIENCE,
                "PA_PUBLIC_ORIGIN": "https://assistant.example.test",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        # Simulate an instance whose normalization invariant was bypassed.
        object.__setattr__(settings, "cf_access_team_domain", "team.cloudflareaccess.com")
        key = generate_key()
        jwk = jwk_for(key, KID)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"keys": [jwk]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            verifier = cloudflare_access_verifier_from_settings(settings, http_client=client)
            await verifier.verify(encode_token(key, now=time.time()))
        self.assertEqual(CERTS_URL, str(requests[0].url))

    async def test_factory_rejects_incomplete_settings(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        with self.assertRaises(ConfigurationError):
            cloudflare_access_verifier_from_settings(settings)


if __name__ == "__main__":
    unittest.main()
