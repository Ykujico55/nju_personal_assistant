"""Infrastructure-side contracts for access-token verifiers (F03).

The transport-independent identity types live in :mod:`personal_assistant.core.auth`;
only the key source and its crypto-specific failure type live here.
"""

from __future__ import annotations

from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from personal_assistant.core.auth import AccessTokenRejectedError


class UnknownSigningKeyError(AccessTokenRejectedError):
    """The token names a ``kid`` that is not in the trusted key set."""


class JwksProvider(Protocol):
    async def public_key(self, kid: str) -> RSAPublicKey: ...
