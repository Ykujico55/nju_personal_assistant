"""Conservative structural redaction for diagnostics and audit metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_SECRET_KEYS = {
    "authorization",
    "cookie",
    "password",
    "private_key",
    "secret",
    "set-cookie",
    "token",
}


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SECRET_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]
    return value

