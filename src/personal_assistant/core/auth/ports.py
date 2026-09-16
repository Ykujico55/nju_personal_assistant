"""Transport-independent verified-identity contract (F03).

The API security boundary consumes these types; production verifiers live in
``infrastructure`` and implement the protocol.  This module must stay free of
HTTP, JWT and cryptography dependencies so ``core`` keeps its dependency
direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Upper bound for a single presented access token (bytes, UTF-8).
MAX_TOKEN_BYTES = 8192


class AccessTokenError(Exception):
    """Base class for failures raised while authenticating a remote request."""


class AccessTokenRejectedError(AccessTokenError):
    """The presented token is missing, malformed or not trustworthy (HTTP 401)."""


class AccessTokenUnavailableError(AccessTokenError):
    """The verifier or key source is temporarily unavailable (HTTP 503)."""


@dataclass(frozen=True, slots=True)
class AccessIdentity:
    """Identity established only from cryptographically verified claims."""

    subject: str
    email: str | None = None


class AccessTokenVerifier(Protocol):
    async def verify(self, token: str) -> AccessIdentity: ...

    async def aclose(self) -> None: ...
