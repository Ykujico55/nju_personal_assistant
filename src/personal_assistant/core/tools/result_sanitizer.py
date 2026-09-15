"""Fail-closed limits for untrusted extension results."""

from __future__ import annotations

import json
from typing import Any

from personal_assistant.core.approvals.canonicalize import normalize_json
from personal_assistant.domain.errors import ValidationError

DEFAULT_MAX_RESULT_BYTES = 256 * 1024


def sanitize_result(value: Any, *, max_bytes: int = DEFAULT_MAX_RESULT_BYTES) -> Any:
    normalized = normalize_json(value)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValidationError(
            f"tool result is {len(encoded)} bytes; maximum is {max_bytes}"
        )
    return normalized
