"""Access-token verification adapters.

Transport-independent identity and verifier contracts are re-exported from
:mod:`personal_assistant.core.auth`; the Cloudflare-specific JWKS provider and
verifier implementation are defined here.
"""

from personal_assistant.core.auth import (
    MAX_TOKEN_BYTES,
    AccessIdentity,
    AccessTokenError,
    AccessTokenRejectedError,
    AccessTokenUnavailableError,
    AccessTokenVerifier,
)

from .cloudflare_access import (
    ACCESS_ALGORITHM,
    ACCESS_CERTS_PATH,
    CLOCK_SKEW_LEEWAY_SECONDS,
    CloudflareAccessTokenVerifier,
    CloudflareJwksProvider,
    cloudflare_access_verifier_from_settings,
)
from .contract import JwksProvider, UnknownSigningKeyError

__all__ = [
    "ACCESS_ALGORITHM",
    "ACCESS_CERTS_PATH",
    "CLOCK_SKEW_LEEWAY_SECONDS",
    "MAX_TOKEN_BYTES",
    "AccessIdentity",
    "AccessTokenError",
    "AccessTokenRejectedError",
    "AccessTokenUnavailableError",
    "AccessTokenVerifier",
    "CloudflareAccessTokenVerifier",
    "CloudflareJwksProvider",
    "JwksProvider",
    "UnknownSigningKeyError",
    "cloudflare_access_verifier_from_settings",
]
