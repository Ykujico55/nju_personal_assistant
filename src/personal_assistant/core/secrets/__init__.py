"""Opaque secret handles; raw values stay inside infrastructure brokers."""

from .handles import SecretHandle
from .store import SecretStorePort, SecretUnavailableError

__all__ = ["SecretHandle", "SecretStorePort", "SecretUnavailableError"]
