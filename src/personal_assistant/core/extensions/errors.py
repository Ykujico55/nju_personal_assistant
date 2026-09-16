"""Typed failures at the extension boundary."""

from typing import Any


class ExtensionError(Exception):
    pass


class ManifestValidationError(ExtensionError, ValueError):
    pass


class DuplicateCapabilityError(ExtensionError):
    pass


class InvalidLifecycleTransition(ExtensionError):
    pass


class ConfirmationRequiredError(ExtensionError):
    pass


class ExtensionOperationError(ExtensionError):
    """Failure carrying a safe, persistable diagnostic code.

    Only codes from ``operations.DIAGNOSTIC_CODES`` may reach the database or an
    API response; raw third-party messages and stack traces never do.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RpcCallError(ExtensionError):
    def __init__(
        self, code: int, message: str, data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data or {}


class RpcTimeoutError(ExtensionError, TimeoutError):
    pass
