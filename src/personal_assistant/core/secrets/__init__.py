"""Opaque secret handles; raw values stay inside infrastructure brokers."""

from .handles import SecretHandle
from .store import SecretStorePort

__all__ = ["SecretHandle", "SecretStorePort"]

