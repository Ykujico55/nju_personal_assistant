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


class RpcCallError(ExtensionError):
    def __init__(
        self, code: int, message: str, data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data or {}


class RpcTimeoutError(ExtensionError, TimeoutError):
    pass
