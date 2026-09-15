"""Deterministic JSON canonicalization for approval binding.

Only JSON values are accepted.  Silently stringifying arbitrary Python objects is
forbidden because two processes could otherwise approve and execute different
representations of the same object.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from personal_assistant.domain.errors import ValidationError


class CanonicalizationError(ValidationError):
    code = "canonicalization_error"


def normalize_json(value: Any, *, _path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(f"{_path}: NaN and Infinity are not allowed")
        # JSON has one number type.  Preserve finite Python floats and use the
        # encoder's stable shortest representation.
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key in sorted(value.keys()):
            if not isinstance(key, str):
                raise CanonicalizationError(f"{_path}: object keys must be strings")
            normalized[key] = normalize_json(value[key], _path=f"{_path}.{key}")
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            normalize_json(item, _path=f"{_path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise CanonicalizationError(
        f"{_path}: unsupported value type {type(value).__name__}; use JSON values"
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        normalize_json(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
