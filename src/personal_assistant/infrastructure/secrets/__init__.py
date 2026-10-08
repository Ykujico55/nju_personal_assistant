"""Secret boundary implementations."""

from .factory import default_secret_store
from .unavailable import UnavailableSecretStore

__all__ = ["UnavailableSecretStore", "default_secret_store"]
