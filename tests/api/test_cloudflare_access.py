"""F03 API boundary tests for Cloudflare Access JWT enforcement.

Tokens and signing keys are generated at runtime; the JWKS endpoint is an
in-memory httpx transport.  No real Cloudflare endpoint or credential is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import unittest
from unittest.mock import patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from personal_assistant.api.middleware import (
    CloudflareAccessBoundaryMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from personal_assistant.app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.infrastructure.auth import (
    AccessIdentity,
    AccessTokenUnavailableError,
    CloudflareAccessTokenVerifier,
    CloudflareJwksProvider,
)
from personal_assistant.settings import ConfigurationError, Settings

ISSUER = "https://team.cloudflareaccess.com"
CERTS_URL = f"{ISSUER}/cdn-cgi/access/certs"
AUDIENCE = "application-audience-tag"
ORIGIN = "https://assistant.example.test"
KID = "test-signing-key-1"


def trusted_settings(**overrides: str) -> Settings:
    environment = {
        "PA_TRUST_CLOUDFLARE_ACCESS": "true",
        "PA_CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
        "PA_CF_ACCESS_AUD": AUDIENCE,
        "PA_PUBLIC_ORIGIN": ORIGIN,
        **overrides,
    }
    with patch.dict(os.environ, environment, clear=True):
        return Settings.from_env()


def generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk_for(private_key: rsa.RSAPrivateKey, kid: str = KID) -> dict[str, object]:
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})
    return jwk


def make_token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = KID,
    subject: str = "subject-123",
    email: str | None = "owner@example.test",
    issuer: str = ISSUER,
    audience: object = AUDIENCE,
    expires_in: float = 300,
) -> str:
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "exp": int(time.time() + expires_in),
    }
    if email is not None:
        claims["email"] = email
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


class UnavailableVerifier:
    def __init__(self) -> None:
        self.closed = False

    async def verify(self, token: str) -> AccessIdentity:
        del token
        raise AccessTokenUnavailableError("test verifier outage")

    async def aclose(self) -> None:
        self.closed = True


class RecordingVerifier:
    def __init__(self, identity: AccessIdentity) -> None:
        self.identity = identity
        self.tokens: list[str] = []

    async def verify(self, token: str) -> AccessIdentity:
        self.tokens.append(token)
        return self.identity

    async def aclose(self) -> None:
        return None


class RealVerifierFactory:
    def __init__(self) -> None:
        self.private_key = generate_key()
        self.rotation_key: rsa.RSAPrivateKey | None = None
        self.rotation_kid = "rotated-signing-key"
        self.transport = httpx.MockTransport(self._handle)
        self.client = httpx.AsyncClient(transport=self.transport)
        provider = CloudflareJwksProvider(certs_url=CERTS_URL, http_client=self.client)
        self.verifier = CloudflareAccessTokenVerifier(
            provider=provider, issuer=ISSUER, audience=AUDIENCE
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        keys = [jwk_for(self.private_key)]
        if self.rotation_key is not None:
            keys.append(jwk_for(self.rotation_key, self.rotation_kid))
        return httpx.Response(200, json={"keys": keys})

    def token(self, **kwargs: object) -> str:
        return make_token(self.private_key, **kwargs)  # type: ignore[arg-type]

    def rotate(self) -> str:
        if self.rotation_key is None:
            self.rotation_key = generate_key()
        return make_token(
            self.rotation_key, kid=self.rotation_kid, email="owner@example.test"
        )

    def close(self) -> None:
        asyncio.run(self.client.aclose())


def whoami_app(settings: Settings, verifier: object) -> FastAPI:
    application = FastAPI()
    application.add_middleware(
        CloudflareAccessBoundaryMiddleware, settings=settings, verifier=verifier
    )
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(SecurityHeadersMiddleware, settings=settings)

    @application.get("/whoami")
    async def whoami(request: Request) -> dict[str, object]:
        return {
            "actor": getattr(request.state, "actor_id", None),
            "email": getattr(request.state, "actor_email", None),
        }

    return application


class CloudflareTokenSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = trusted_settings()
        self.real = RealVerifierFactory()
        self.addCleanup(self.real.close)
        self.valid = self.real.token()
        self.client = TestClient(whoami_app(self.settings, self.real.verifier))

    def test_header_token_sets_verified_actor(self) -> None:
        response = self.client.get("/whoami", headers={"Cf-Access-Jwt-Assertion": self.valid})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("subject-123", response.json()["actor"])
        self.assertEqual("owner@example.test", response.json()["email"])

    def test_cookie_token_is_accepted(self) -> None:
        response = self.client.get("/whoami", cookies={"CF_Authorization": self.valid})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("subject-123", response.json()["actor"])

    def test_identical_header_and_cookie_are_accepted(self) -> None:
        response = self.client.get(
            "/whoami",
            headers={"Cf-Access-Jwt-Assertion": self.valid},
            cookies={"CF_Authorization": self.valid},
        )
        self.assertEqual(200, response.status_code, response.text)

    def test_conflicting_header_and_cookie_are_rejected(self) -> None:
        other = self.real.token(subject="other-subject")
        response = self.client.get(
            "/whoami",
            headers={"Cf-Access-Jwt-Assertion": self.valid},
            cookies={"CF_Authorization": other},
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual(
            "CLOUDFLARE_ACCESS_TOKEN_INVALID", response.json()["error"]["code"]
        )

    def test_duplicate_header_values_are_rejected(self) -> None:
        response = self.client.get(
            "/whoami",
            headers=[
                ("Cf-Access-Jwt-Assertion", self.valid),
                ("Cf-Access-Jwt-Assertion", self.real.token(subject="other-subject")),
            ],
        )
        self.assertEqual(401, response.status_code)

    def test_duplicate_cookie_values_are_rejected(self) -> None:
        response = self.client.get(
            "/whoami",
            headers={
                "Cookie": f"CF_Authorization={self.valid}; CF_Authorization={self.valid}"
            },
        )
        self.assertEqual(401, response.status_code)

    def test_repeated_raw_cookie_headers_are_rejected(self) -> None:
        response = self.client.get(
            "/whoami",
            headers=[
                ("Cookie", f"CF_Authorization={self.valid}"),
                ("Cookie", f"CF_Authorization={self.valid}"),
            ],
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual(
            "CLOUDFLARE_ACCESS_TOKEN_INVALID", response.json()["error"]["code"]
        )

    def test_multiple_cookie_headers_with_one_token_are_accepted(self) -> None:
        response = self.client.get(
            "/whoami",
            headers=[
                ("Cookie", "theme=dark"),
                ("Cookie", f"CF_Authorization={self.valid}"),
            ],
        )
        self.assertEqual(200, response.status_code, response.text)

    def test_forged_identity_headers_do_not_change_actor(self) -> None:
        response = self.client.get(
            "/whoami",
            headers={
                "Cf-Access-Jwt-Assertion": self.valid,
                "Cf-Access-Authenticated-User-Email": "attacker@example.test",
                "X-Forwarded-User": "attacker@example.test",
                "X-Forwarded-Host": "attacker.example.test",
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("subject-123", response.json()["actor"])
        self.assertEqual("owner@example.test", response.json()["email"])

    def test_forged_identity_headers_without_jwt_are_rejected(self) -> None:
        response = self.client.get(
            "/whoami",
            headers={
                "Cf-Access-Authenticated-User-Email": "attacker@example.test",
                "X-Forwarded-User": "attacker@example.test",
            },
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual(
            "CLOUDFLARE_ACCESS_TOKEN_MISSING", response.json()["error"]["code"]
        )

    def test_missing_token_is_rejected_with_request_id_and_security_headers(self) -> None:
        response = self.client.get("/whoami")
        self.assertEqual(401, response.status_code)
        self.assertEqual(
            "CLOUDFLARE_ACCESS_TOKEN_MISSING", response.json()["error"]["code"]
        )
        self.assertTrue(response.headers["X-Request-ID"])
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])

    def test_oversized_token_is_rejected(self) -> None:
        response = self.client.get(
            "/whoami", headers={"Cf-Access-Jwt-Assertion": "a" * 9000}
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual(
            "CLOUDFLARE_ACCESS_TOKEN_INVALID", response.json()["error"]["code"]
        )

    def test_error_body_never_echoes_the_token(self) -> None:
        token = self.real.token(expires_in=-600)
        response = self.client.get("/whoami", headers={"Cf-Access-Jwt-Assertion": token})
        self.assertEqual(401, response.status_code)
        self.assertNotIn(token, response.text)

    def test_development_mode_keeps_local_actor_without_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        client = TestClient(whoami_app(settings, None))
        response = client.get("/whoami")
        self.assertEqual(200, response.status_code)
        self.assertEqual("development-owner", response.json()["actor"])


class CloudflareMiddlewareRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = trusted_settings()
        self.real = RealVerifierFactory()
        self.addCleanup(self.real.close)

    def _client(self, verifier: object) -> TestClient:
        return TestClient(whoami_app(self.settings, verifier))

    def _assert_rejected(self, response: httpx.Response) -> None:
        self.assertEqual(401, response.status_code, response.text)
        self.assertEqual(
            "CLOUDFLARE_ACCESS_TOKEN_INVALID", response.json()["error"]["code"]
        )

    def test_malformed_token_is_rejected(self) -> None:
        self._assert_rejected(
            self._client(self.real.verifier).get(
                "/whoami", headers={"Cf-Access-Jwt-Assertion": "not-a-jwt"}
            )
        )

    def test_alg_none_token_is_rejected(self) -> None:
        token = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "subject-123", "exp": int(time.time() + 300)},
            None,
            algorithm="none",
            headers={"kid": KID},
        )
        self._assert_rejected(
            self._client(self.real.verifier).get(
                "/whoami", headers={"Cf-Access-Jwt-Assertion": token}
            )
        )

    def test_unknown_kid_is_rejected(self) -> None:
        token = self.real.token(kid="unknown-key")
        self._assert_rejected(
            self._client(self.real.verifier).get(
                "/whoami", headers={"Cf-Access-Jwt-Assertion": token}
            )
        )

    def test_wrong_signature_is_rejected(self) -> None:
        rogue = generate_key()
        token = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "subject-123", "exp": int(time.time() + 300)},
            rogue,
            algorithm="RS256",
            headers={"kid": KID},
        )
        self._assert_rejected(
            self._client(self.real.verifier).get(
                "/whoami", headers={"Cf-Access-Jwt-Assertion": token}
            )
        )

    def test_expired_token_is_rejected(self) -> None:
        self._assert_rejected(
            self._client(self.real.verifier).get(
                "/whoami",
                headers={"Cf-Access-Jwt-Assertion": self.real.token(expires_in=-600)},
            )
        )

    def test_future_not_before_is_rejected(self) -> None:
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "subject-123",
            "exp": int(time.time() + 300),
            "nbf": int(time.time() + 3600),
        }
        token = jwt.encode(claims, self.real.private_key, algorithm="RS256", headers={"kid": KID})
        self._assert_rejected(
            self._client(self.real.verifier).get(
                "/whoami", headers={"Cf-Access-Jwt-Assertion": token}
            )
        )

    def test_wrong_issuer_is_rejected(self) -> None:
        token = self.real.token(issuer="https://evil.cloudflareaccess.com")
        self._assert_rejected(
            self._client(self.real.verifier).get(
                "/whoami", headers={"Cf-Access-Jwt-Assertion": token}
            )
        )

    def test_wrong_audience_is_rejected(self) -> None:
        token = self.real.token(audience="other-application")
        self._assert_rejected(
            self._client(self.real.verifier).get(
                "/whoami", headers={"Cf-Access-Jwt-Assertion": token}
            )
        )

    def test_audience_array_without_target_is_rejected(self) -> None:
        token = self.real.token(audience=["other-application"])
        self._assert_rejected(
            self._client(self.real.verifier).get(
                "/whoami", headers={"Cf-Access-Jwt-Assertion": token}
            )
        )

    def test_missing_required_claim_is_rejected(self) -> None:
        token = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time() + 300)},
            self.real.private_key,
            algorithm="RS256",
            headers={"kid": KID},
        )
        self._assert_rejected(
            self._client(self.real.verifier).get(
                "/whoami", headers={"Cf-Access-Jwt-Assertion": token}
            )
        )

    def test_verifier_outage_is_unavailable(self) -> None:
        response = self._client(UnavailableVerifier()).get(
            "/whoami", headers={"Cf-Access-Jwt-Assertion": self.real.token()}
        )
        self.assertEqual(503, response.status_code, response.text)
        self.assertEqual(
            "CLOUDFLARE_ACCESS_UNAVAILABLE", response.json()["error"]["code"]
        )

    def test_real_verifier_jwks_outage_is_unavailable(self) -> None:
        def failing_handler(request: httpx.Request) -> httpx.Response:
            del request
            raise httpx.ConnectError("connection refused")

        unreachable_client = httpx.AsyncClient(
            transport=httpx.MockTransport(failing_handler)
        )
        provider = CloudflareJwksProvider(certs_url=CERTS_URL, http_client=unreachable_client)
        verifier = CloudflareAccessTokenVerifier(
            provider=provider, issuer=ISSUER, audience=AUDIENCE
        )
        try:
            response = self._client(verifier).get(
                "/whoami", headers={"Cf-Access-Jwt-Assertion": self.real.token()}
            )
        finally:
            asyncio.run(unreachable_client.aclose())
        self.assertEqual(503, response.status_code, response.text)
        self.assertEqual(
            "CLOUDFLARE_ACCESS_UNAVAILABLE", response.json()["error"]["code"]
        )

    def test_rotated_key_during_throttle_window_is_retryable_503(self) -> None:
        client = self._client(self.real.verifier)
        attacker = self.real.token(kid="attacker-key")
        first = client.get("/whoami", headers={"Cf-Access-Jwt-Assertion": attacker})
        self.assertEqual(401, first.status_code, first.text)
        rotated = self.real.rotate()
        second = client.get("/whoami", headers={"Cf-Access-Jwt-Assertion": rotated})
        self.assertEqual(503, second.status_code, second.text)
        self.assertEqual(
            "CLOUDFLARE_ACCESS_UNAVAILABLE", second.json()["error"]["code"]
        )
        self.assertTrue(second.json()["error"]["retryable"])
        # The previously cached, still-trusted key is unaffected.
        third = client.get(
            "/whoami", headers={"Cf-Access-Jwt-Assertion": self.real.token()}
        )
        self.assertEqual(200, third.status_code, third.text)

    def test_wrong_verifier_outage_never_falls_back_to_identity_headers(self) -> None:
        response = self._client(UnavailableVerifier()).get(
            "/whoami",
            headers={
                "Cf-Access-Jwt-Assertion": self.real.token(),
                "Cf-Access-Authenticated-User-Email": "attacker@example.test",
            },
        )
        self.assertEqual(503, response.status_code)


class PublicAppBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = trusted_settings()
        self.real = RealVerifierFactory()
        self.addCleanup(self.real.close)
        self.valid = self.real.token()
        self.client = TestClient(
            create_app(
                settings=self.settings,
                container=build_container(self.settings),
                access_verifier=self.real.verifier,
            )
        )

    def test_valid_jwt_can_reach_public_health(self) -> None:
        response = self.client.get(
            "/healthz", headers={"Cf-Access-Jwt-Assertion": self.valid}
        )
        self.assertEqual(200, response.status_code, response.text)

    def test_valid_jwt_can_reach_public_api(self) -> None:
        response = self.client.get(
            "/api/v1/extensions", headers={"Cf-Access-Jwt-Assertion": self.valid}
        )
        self.assertEqual(200, response.status_code, response.text)

    def test_missing_jwt_is_rejected_before_routing(self) -> None:
        response = self.client.get("/api/v1/extensions")
        self.assertEqual(401, response.status_code)
        self.assertEqual(
            "CLOUDFLARE_ACCESS_TOKEN_MISSING", response.json()["error"]["code"]
        )

    def test_public_app_never_exposes_admin_extension_routes(self) -> None:
        headers = {"Cf-Access-Jwt-Assertion": self.valid}
        self.assertEqual(
            404, self.client.get("/admin/v1/extensions", headers=headers).status_code
        )
        self.assertEqual(
            404,
            self.client.get(
                "/admin/v1/extension-operations/anything", headers=headers
            ).status_code,
        )
        self.assertEqual(
            404,
            self.client.post(
                "/admin/v1/extensions/install",
                json={},
                headers={**headers, "Idempotency-Key": "public-install"},
            ).status_code,
        )
        self.assertEqual(
            404,
            self.client.post(
                "/admin/v1/extensions/example.echo/enable",
                headers={**headers, "Idempotency-Key": "public-enable"},
            ).status_code,
        )
        self.assertEqual(
            404,
            self.client.post(
                "/admin/v1/extensions/example.echo/upgrade",
                json={},
                headers={**headers, "Idempotency-Key": "public-upgrade"},
            ).status_code,
        )
        self.assertEqual(
            404,
            self.client.delete(
                "/admin/v1/extensions/example.echo",
                headers={**headers, "Idempotency-Key": "public-uninstall"},
            ).status_code,
        )
        self.assertEqual(
            404,
            self.client.post(
                "/admin/v1/extensions/example.echo/purge-data",
                headers={**headers, "Idempotency-Key": "public-purge"},
            ).status_code,
        )

    def test_csrf_gate_still_applies_to_authenticated_commands(self) -> None:
        response = self.client.post(
            "/api/v1/tasks",
            json={"objective": "csrf counterexample"},
            headers={
                "Cf-Access-Jwt-Assertion": self.valid,
                "Idempotency-Key": "csrf-one",
            },
        )
        self.assertEqual(403, response.status_code, response.text)
        self.assertEqual("CSRF_CHECK_FAILED", response.json()["error"]["code"])

    def test_authenticated_command_passes_csrf_and_creates_task(self) -> None:
        response = self.client.post(
            "/api/v1/tasks",
            json={"objective": "authenticated task"},
            headers={
                "Cf-Access-Jwt-Assertion": self.valid,
                "Idempotency-Key": "csrf-two",
                "Origin": ORIGIN,
                "Sec-Fetch-Site": "same-origin",
                "X-Requested-With": "personal-assistant-pwa",
            },
        )
        self.assertEqual(202, response.status_code, response.text)

    def test_development_public_app_is_unaffected(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        client = TestClient(
            create_app(settings=settings, container=build_container(settings))
        )
        self.assertEqual(200, client.get("/api/v1/extensions").status_code)

    def test_injected_settings_are_revalidated(self) -> None:
        settings = trusted_settings()
        object.__setattr__(settings, "cf_access_team_domain", "https://attacker.example")
        with self.assertRaises(ConfigurationError):
            build_container(settings)
        with self.assertRaises(ConfigurationError):
            create_app(settings=settings)

    def test_recording_verifier_receives_the_raw_token_once(self) -> None:
        recorder = RecordingVerifier(AccessIdentity(subject="recorded-subject"))
        client = TestClient(
            create_app(
                settings=self.settings,
                container=build_container(self.settings),
                access_verifier=recorder,
            )
        )
        response = client.get(
            "/healthz", headers={"Cf-Access-Jwt-Assertion": self.valid}
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual([self.valid], recorder.tokens)


class ShutdownResilienceTests(unittest.TestCase):
    def test_verifier_close_failure_does_not_skip_storage_close(self) -> None:
        settings = trusted_settings()
        container = build_container(settings)

        class TrackingStorage:
            def __init__(self) -> None:
                self.started = False
                self.closed = False

            async def startup(self) -> None:
                self.started = True

            async def close(self) -> None:
                self.closed = True

        class FailingCloseVerifier:
            async def verify(self, token: str) -> AccessIdentity:
                del token
                raise AccessTokenUnavailableError("unused")

            async def aclose(self) -> None:
                raise RuntimeError("verifier close failed")

        storage = TrackingStorage()
        container.storage = storage  # type: ignore[assignment]
        application = create_app(
            settings=settings,
            container=container,
            access_verifier=FailingCloseVerifier(),
        )
        with self.assertRaises(RuntimeError), TestClient(application):
            pass
        self.assertTrue(storage.started)
        self.assertTrue(storage.closed)


if __name__ == "__main__":
    unittest.main()
