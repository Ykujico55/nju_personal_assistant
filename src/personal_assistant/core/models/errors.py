"""Typed failures for model provider adapters.

Adapters in ``infrastructure`` translate transport-level failures into these
types.  Messages never contain prompts, field values, credentials or vendor
response bodies; only bounded identifiers and status codes.
"""

from __future__ import annotations


class ModelProviderError(RuntimeError):
    code = "MODEL_PROVIDER_ERROR"
    retryable = False

    def __init__(self, message: str, *, provider_id: str | None = None) -> None:
        super().__init__(message)
        self.provider_id = provider_id


class ModelProviderUnavailableError(ModelProviderError):
    """Connection, DNS, TLS or deadline failure; the remote outcome is unknown."""

    code = "MODEL_PROVIDER_UNAVAILABLE"
    retryable = True


class ModelProviderTimeoutError(ModelProviderUnavailableError):
    code = "MODEL_PROVIDER_TIMEOUT"


class ModelProviderRejectedError(ModelProviderError):
    """The provider answered with a non-success status or an explicit error."""

    code = "MODEL_PROVIDER_REJECTED"

    def __init__(
        self,
        message: str,
        *,
        provider_id: str | None = None,
        status_code: int | None = None,
        rejection_code: str | None = None,
    ) -> None:
        super().__init__(message, provider_id=provider_id)
        self.status_code = status_code
        # A fixed, adapter-owned classification only.  The provider response
        # body is never echoed here: a vendor string could otherwise carry an
        # API key or sensitive field value straight into an exception.
        self.rejection_code = rejection_code


class ModelProviderProtocolError(ModelProviderError):
    """The provider response was malformed, incomplete or not JSON."""

    code = "MODEL_PROVIDER_PROTOCOL_ERROR"


class ModelProviderResponseTooLargeError(ModelProviderProtocolError):
    code = "MODEL_PROVIDER_RESPONSE_TOO_LARGE"


class ModelCredentialUnavailableError(ModelProviderError):
    """The host broker could not resolve the bound SecretHandle (fail closed)."""

    code = "MODEL_CREDENTIAL_UNAVAILABLE"
