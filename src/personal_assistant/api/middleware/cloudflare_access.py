"""Cloudflare Access boundary for the public API/PWA app (F03).

The middleware only extracts one token from the two official carriers, calls the
injected verifier and maps the result to a stable error envelope.  Identity is
established exclusively from cryptographically verified claims; no request
header, forwarded value or email header is trusted.  Admin (8001) and
health-only (8010) apps never mount this middleware.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from personal_assistant.core.auth import (
    MAX_TOKEN_BYTES,
    AccessIdentity,
    AccessTokenRejectedError,
    AccessTokenUnavailableError,
    AccessTokenVerifier,
)
from personal_assistant.settings import Settings

ACCESS_TOKEN_HEADER = "Cf-Access-Jwt-Assertion"
ACCESS_TOKEN_COOKIE = "CF_Authorization"
DEVELOPMENT_ACTOR_ID = "development-owner"
TOKEN_MISSING_CODE = "CLOUDFLARE_ACCESS_TOKEN_MISSING"
TOKEN_INVALID_CODE = "CLOUDFLARE_ACCESS_TOKEN_INVALID"
ACCESS_UNAVAILABLE_CODE = "CLOUDFLARE_ACCESS_UNAVAILABLE"


def _cookie_values(request: Request) -> list[str]:
    values: list[str] = []
    # Aggregate every raw ``Cookie`` header field; a server may deliver repeated
    # cookie headers separately and each one can carry its own CF_Authorization.
    for header in request.headers.getlist("Cookie"):
        for fragment in header.split(";"):
            name, separator, value = fragment.strip().partition("=")
            if separator and name == ACCESS_TOKEN_COOKIE:
                values.append(value)
    return values


def _extract_token(request: Request) -> str | None:
    """Return the single presented token, or ``None`` when none was sent.

    Multiple or disagreeing carriers are ambiguous and rejected instead of
    picking one arbitrarily.
    """

    header_values = request.headers.getlist(ACCESS_TOKEN_HEADER)
    if len(header_values) > 1:
        raise AccessTokenRejectedError("ambiguous Access token header")
    cookie_values = _cookie_values(request)
    if len(cookie_values) > 1:
        raise AccessTokenRejectedError("ambiguous Access token cookie")
    header_token = header_values[0] if header_values else None
    cookie_token = cookie_values[0] if cookie_values else None
    if header_token is not None and cookie_token is not None and header_token != cookie_token:
        raise AccessTokenRejectedError("conflicting Access token carriers")
    token = header_token if header_token is not None else cookie_token
    if token is None:
        return None
    if not token or len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
        raise AccessTokenRejectedError("missing or oversized Access token")
    return token


class CloudflareAccessBoundaryMiddleware(BaseHTTPMiddleware):
    """Fail-closed seam in front of every public route when Access is trusted."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        verifier: AccessTokenVerifier | None = None,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._verifier = verifier

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self._settings.trust_cloudflare_access:
            request.state.actor_id = DEVELOPMENT_ACTOR_ID
            return await call_next(request)
        if self._verifier is None:
            return self._error(
                request,
                503,
                ACCESS_UNAVAILABLE_CODE,
                "Cloudflare Access verification is unavailable.",
                retryable=True,
            )
        try:
            token = _extract_token(request)
        except AccessTokenRejectedError:
            return self._error(
                request,
                401,
                TOKEN_INVALID_CODE,
                "The Cloudflare Access token was rejected.",
                retryable=False,
            )
        if token is None:
            return self._error(
                request,
                401,
                TOKEN_MISSING_CODE,
                "A Cloudflare Access token is required.",
                retryable=False,
            )
        try:
            identity = await self._verifier.verify(token)
        except AccessTokenUnavailableError:
            return self._error(
                request,
                503,
                ACCESS_UNAVAILABLE_CODE,
                "Cloudflare Access verification is unavailable.",
                retryable=True,
            )
        except AccessTokenRejectedError:
            return self._error(
                request,
                401,
                TOKEN_INVALID_CODE,
                "The Cloudflare Access token was rejected.",
                retryable=False,
            )
        self._set_identity(request, identity)
        return await call_next(request)

    @staticmethod
    def _set_identity(request: Request, identity: AccessIdentity) -> None:
        request.state.actor_id = identity.subject
        request.state.access_identity = identity
        if identity.email is not None:
            request.state.actor_email = identity.email

    @staticmethod
    def _error(
        request: Request,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": getattr(request.state, "request_id", "unknown"),
                    "retryable": retryable,
                    "details": {},
                }
            },
        )
