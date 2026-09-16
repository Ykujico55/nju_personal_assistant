"""Cloudflare Access JWT verification and JWKS retrieval (F03).

Cloudflare sends an application token in the ``Cf-Access-Jwt-Assertion`` request
header and, for browser requests, the ``CF_Authorization`` cookie
(https://developers.cloudflare.com/cloudflare-one/ access-controls/applications/
http-apps/authorization-cookie/validating-json/).  Signing keys are published as
a JWKS document at ``https://<team>.cloudflareaccess.com/cdn-cgi/access/certs``.

This module performs signature/issuer/audience/claim verification with the
mature ``PyJWT`` + ``cryptography`` stack.  It never logs tokens or keys, never
constructs URLs from request input and fails closed on any key retrieval error.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm

from personal_assistant.core.auth import (
    MAX_TOKEN_BYTES,
    AccessIdentity,
    AccessTokenRejectedError,
    AccessTokenUnavailableError,
)
from personal_assistant.infrastructure.auth.contract import (
    JwksProvider,
    UnknownSigningKeyError,
)
from personal_assistant.settings import (
    ConfigurationError,
    Settings,
    normalize_audience,
    normalize_team_domain,
)

ACCESS_CERTS_PATH = "/cdn-cgi/access/certs"
ACCESS_ALGORITHM = "RS256"

# Fixed, deliberately small bounds; none of these are operator-configurable.
CLOCK_SKEW_LEEWAY_SECONDS = 30
DEFAULT_CACHE_TTL_SECONDS = 300.0
MIN_REFRESH_INTERVAL_SECONDS = 30.0
DEFAULT_FETCH_TIMEOUT_SECONDS = 5.0
MAX_JWKS_RESPONSE_BYTES = 64 * 1024
MAX_KID_LENGTH = 256
MAX_SUBJECT_LENGTH = 256
MAX_EMAIL_LENGTH = 320


class CloudflareJwksProvider:
    """Bounded JWKS cache with single-flight, throttled refresh.

    Semantics (F03 contract):

    - A key found in the cache is trusted only while the cache entry is
      unexpired (``cache_ttl_seconds``); there is no stale-on-error fallback.
    - An unknown ``kid`` triggers at most one refresh per refresh interval.
    - Concurrent refresh attempts are coalesced into a single network call.
    - A completed refresh that does not contain the ``kid`` raises
      :class:`UnknownSigningKeyError` (401).  A ``kid`` that could not be
      checked because the refresh was throttled, failed or the cached key is
      stale raises :class:`AccessTokenUnavailableError` (503, retryable), so a
      key rotation during the throttle window is never reported as a permanent
      identity failure.
    - Network failure, timeout, non-2xx, oversized, non-JSON or malformed JWKS
      data raises :class:`AccessTokenUnavailableError` (fail closed).
    """

    def __init__(
        self,
        *,
        certs_url: str,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        min_refresh_interval_seconds: float = MIN_REFRESH_INTERVAL_SECONDS,
        fetch_timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_JWKS_RESPONSE_BYTES,
    ) -> None:
        self._certs_url = certs_url
        self._client = http_client
        self._owns_client = http_client is None
        self._clock = clock
        self._cache_ttl_seconds = cache_ttl_seconds
        self._min_refresh_interval_seconds = min_refresh_interval_seconds
        self._fetch_timeout_seconds = fetch_timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._keys: dict[str, RSAPublicKey] = {}
        self._fetched_at: float | None = None
        self._last_attempt_at: float | None = None
        self._last_unknown_refresh_at: float | None = None
        self._generation = 0
        self._lock = asyncio.Lock()

    async def public_key(self, kid: str) -> RSAPublicKey:
        now = self._clock()
        cached = self._keys.get(kid)
        if cached is not None and self._is_fresh(now):
            return cached
        observed_generation = self._generation
        refreshed = False
        async with self._lock:
            if self._generation != observed_generation:
                # Another caller completed a refresh while we waited for the
                # lock; the current key set is authoritative.
                refreshed = True
            elif self._may_attempt_refresh(
                self._clock(),
                cached_key_present=cached is not None,
                bootstrap=self._fetched_at is None,
            ):
                attempt_at = self._clock()
                previous_attempt_at = self._last_attempt_at
                previous_unknown_refresh_at = self._last_unknown_refresh_at
                self._last_attempt_at = attempt_at
                if cached is None and self._fetched_at is not None:
                    self._last_unknown_refresh_at = attempt_at
                try:
                    keys = await self._fetch_keys()
                except AccessTokenUnavailableError:
                    pass
                except BaseException:
                    # Cancellation must not poison the refresh throttle; waiters
                    # and later requests have to be able to retry immediately.
                    self._last_attempt_at = previous_attempt_at
                    self._last_unknown_refresh_at = previous_unknown_refresh_at
                    raise
                else:
                    self._keys = keys
                    self._fetched_at = self._clock()
                    self._generation += 1
                    refreshed = True
                    if kid not in keys:
                        self._last_unknown_refresh_at = self._clock()
        now = self._clock()
        cached = self._keys.get(kid)
        if cached is not None and self._is_fresh(now):
            return cached
        if refreshed:
            # A completed refresh confirmed that this kid is not trusted.
            raise UnknownSigningKeyError("the token references an unknown signing key")
        # The kid could not be checked (refresh throttled, failed or the cached
        # key is stale): a transient rotation state, not a permanent failure.
        raise AccessTokenUnavailableError(
            "Cloudflare Access signing keys could not be refreshed in time"
        )

    async def aclose(self) -> None:
        client = self._client
        owned = self._owns_client
        self._client = None
        self._owns_client = False
        if client is not None and owned:
            await client.aclose()

    def _is_fresh(self, now: float) -> bool:
        return self._fetched_at is not None and (now - self._fetched_at) < self._cache_ttl_seconds

    def _may_attempt_refresh(
        self, now: float, *, cached_key_present: bool, bootstrap: bool
    ) -> bool:
        if cached_key_present or bootstrap:
            # First load and stale-known-key refresh share one attempt throttle.
            if self._last_attempt_at is None:
                return True
            return (now - self._last_attempt_at) >= self._min_refresh_interval_seconds
        # A genuinely unknown kid may trigger one controlled refresh for key
        # rotation, even right after a successful cache fill, but never a storm.
        if self._last_unknown_refresh_at is None:
            return True
        return (now - self._last_unknown_refresh_at) >= self._min_refresh_interval_seconds

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._fetch_timeout_seconds),
                follow_redirects=False,
                headers={"Accept": "application/json"},
            )
            self._owns_client = True
        return self._client

    async def _fetch_keys(self) -> dict[str, RSAPublicKey]:
        client = self._ensure_client()
        try:
            # A single monotonic deadline bounds the whole request including
            # slow-drip bodies; httpx read timeouts only bound individual chunks.
            async with asyncio.timeout(self._fetch_timeout_seconds):
                # Redirects are disabled per request, not only on the client the
                # provider creates itself, so an injected client cannot redirect
                # key retrieval away from the official host.
                async with client.stream(
                    "GET", self._certs_url, follow_redirects=False
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise AccessTokenUnavailableError(
                            "Cloudflare Access certs endpoint returned a non-success status"
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._max_response_bytes:
                            raise AccessTokenUnavailableError(
                                "Cloudflare Access certs response exceeded the size limit"
                            )
        except TimeoutError as exc:
            raise AccessTokenUnavailableError(
                "Cloudflare Access certs endpoint timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise AccessTokenUnavailableError(
                "Cloudflare Access certs endpoint is unreachable"
            ) from exc
        return self._parse_keys(bytes(body))

    def _parse_keys(self, body: bytes) -> dict[str, RSAPublicKey]:
        try:
            payload: Any = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise AccessTokenUnavailableError(
                "Cloudflare Access certs response is not valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise AccessTokenUnavailableError("Cloudflare Access certs response is malformed")
        entries = payload.get("keys")
        if not isinstance(entries, list) or not entries:
            raise AccessTokenUnavailableError("Cloudflare Access certs response has no keys")
        parsed: dict[str, RSAPublicKey] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise AccessTokenUnavailableError("Cloudflare Access JWKS entry is malformed")
            kid = entry.get("kid")
            if not isinstance(kid, str) or not kid or len(kid) > MAX_KID_LENGTH:
                raise AccessTokenUnavailableError("Cloudflare Access JWKS entry has no key id")
            if (
                entry.get("kty") != "RSA"
                or entry.get("use") != "sig"
                or entry.get("alg") != ACCESS_ALGORITHM
            ):
                raise AccessTokenUnavailableError(
                    "Cloudflare Access JWKS entry is not an RS256 signing key"
                )
            if kid in parsed:
                raise AccessTokenUnavailableError("Cloudflare Access JWKS has a duplicate key id")
            try:
                key = RSAAlgorithm.from_jwk(json.dumps(entry))
            except Exception as exc:  # noqa: BLE001 - any key parse failure is unavailable
                raise AccessTokenUnavailableError(
                    "Cloudflare Access JWKS entry is not a valid RSA key"
                ) from exc
            if not isinstance(key, RSAPublicKey):
                raise AccessTokenUnavailableError(
                    "Cloudflare Access JWKS entry is not an RSA public key"
                )
            parsed[kid] = key
        return parsed


class CloudflareAccessTokenVerifier:
    """Verify Cloudflare Access tokens against an injected JWKS provider."""

    def __init__(
        self,
        *,
        provider: JwksProvider,
        issuer: str,
        audience: str,
        clock: Callable[[], float] = time.time,
        leeway_seconds: int = CLOCK_SKEW_LEEWAY_SECONDS,
    ) -> None:
        self._provider = provider
        self._issuer = issuer
        self._audience = audience
        self._clock = clock
        self._leeway_seconds = leeway_seconds

    async def verify(self, token: str) -> AccessIdentity:
        if (
            not isinstance(token, str)
            or not token
            or len(token.encode("utf-8")) > MAX_TOKEN_BYTES
        ):
            raise AccessTokenRejectedError("the Access token is missing or oversized")
        try:
            header: Any = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AccessTokenRejectedError("the Access token is malformed") from exc
        if not isinstance(header, dict) or header.get("alg") != ACCESS_ALGORITHM:
            raise AccessTokenRejectedError("the Access token uses an unsupported algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid.strip() or len(kid) > MAX_KID_LENGTH:
            raise AccessTokenRejectedError("the Access token is missing a signing key id")

        key = await self._provider.public_key(kid)
        try:
            claims: Any = jwt.decode(
                token,
                key=key,
                algorithms=[ACCESS_ALGORITHM],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": ["exp", "sub"],
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError as exc:
            raise AccessTokenRejectedError("the Access token failed verification") from exc
        if not isinstance(claims, dict):
            raise AccessTokenRejectedError("the Access token claims are malformed")
        self._validate_time_claims(claims)
        return AccessIdentity(
            subject=self._verified_subject(claims),
            email=self._verified_email(claims),
        )

    async def aclose(self) -> None:
        close = getattr(self._provider, "aclose", None)
        if close is not None:
            await close()

    def _validate_time_claims(self, claims: dict[str, Any]) -> None:
        now = self._clock()
        expiry = self._time_claim(claims, "exp", required=True)
        if expiry is None:
            raise AccessTokenRejectedError("the Access token has no expiry")
        if expiry < now - self._leeway_seconds:
            raise AccessTokenRejectedError("the Access token is expired")
        not_before = self._time_claim(claims, "nbf", required=False)
        if not_before is not None:
            if not_before > now + self._leeway_seconds:
                raise AccessTokenRejectedError("the Access token is not yet valid")
            if expiry <= not_before:
                raise AccessTokenRejectedError("the Access token time claims are inconsistent")
        issued_at = self._time_claim(claims, "iat", required=False)
        if issued_at is not None:
            if issued_at > now + self._leeway_seconds:
                raise AccessTokenRejectedError("the Access token was issued in the future")
            if expiry <= issued_at:
                raise AccessTokenRejectedError("the Access token time claims are inconsistent")

    def _time_claim(
        self, claims: dict[str, Any], name: str, *, required: bool
    ) -> float | None:
        value = claims.get(name)
        if value is None:
            if required:
                raise AccessTokenRejectedError(f"the Access token has no {name} claim")
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AccessTokenRejectedError(f"the Access token {name} claim is not numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0:
            raise AccessTokenRejectedError(f"the Access token {name} claim is out of range")
        return numeric

    def _verified_subject(self, claims: dict[str, Any]) -> str:
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise AccessTokenRejectedError("the Access token has no verified subject")
        if len(subject) > MAX_SUBJECT_LENGTH:
            raise AccessTokenRejectedError("the Access token subject is oversized")
        return subject

    def _verified_email(self, claims: dict[str, Any]) -> str | None:
        email = claims.get("email")
        if email is None:
            return None
        if not isinstance(email, str) or len(email) > MAX_EMAIL_LENGTH:
            raise AccessTokenRejectedError("the Access token email claim is malformed")
        normalized = email.strip()
        return normalized or None


def cloudflare_access_verifier_from_settings(
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
    clock: Callable[[], float] | None = None,
) -> CloudflareAccessTokenVerifier:
    """Compose the production verifier from validated settings.

    ``Settings.__post_init__`` validates every construction path, but this
    factory still normalizes the team domain and audience itself and never uses
    the raw field values, so an injected ``Settings`` instance cannot steer the
    JWKS URL or issuer to an arbitrary host.
    """

    raw_team_domain = settings.cf_access_team_domain
    raw_audience = settings.cf_access_aud
    if not raw_team_domain or not raw_audience:
        raise ConfigurationError("Cloudflare Access configuration is incomplete")
    team_domain = normalize_team_domain(raw_team_domain)
    audience = normalize_audience(raw_audience)
    provider = CloudflareJwksProvider(
        certs_url=f"{team_domain}{ACCESS_CERTS_PATH}",
        http_client=http_client,
        clock=clock or time.monotonic,
    )
    return CloudflareAccessTokenVerifier(
        provider=provider,
        issuer=team_domain,
        audience=audience,
        clock=clock or time.time,
    )
