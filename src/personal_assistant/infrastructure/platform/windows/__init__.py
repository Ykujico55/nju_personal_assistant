"""Windows host adapters; importing this package does not load a native DLL."""

from .credential_manager import WindowsCredentialManager

__all__ = ["WindowsCredentialManager"]
