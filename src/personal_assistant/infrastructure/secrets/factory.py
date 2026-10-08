"""Platform selection at the infrastructure boundary, never a memory fallback."""
from __future__ import annotations

import sys

from personal_assistant.core.secrets import SecretStorePort

from .unavailable import UnavailableSecretStore


def default_secret_store() -> SecretStorePort:
    if sys.platform != "win32":
        return UnavailableSecretStore()
    from personal_assistant.infrastructure.platform.windows import WindowsCredentialManager

    return WindowsCredentialManager()
