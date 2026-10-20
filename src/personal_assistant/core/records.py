"""Versioned generic records and exactly bound command receipts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from personal_assistant.domain import ValidationError

Command = Callable[[], Awaitable[dict[str, Any]]]


def fingerprint(value: object) -> str:
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError):
        raise ValidationError("command must be canonical JSON") from None
    if len(raw) > 1_000_000:
        raise ValidationError("command is too large")
    return hashlib.sha256(raw).hexdigest()


class RecordStore(Protocol):
    async def get(self, bucket: str, key: str) -> dict[str, Any] | None: ...
    async def list(self, bucket: str) -> tuple[dict[str, Any], ...]: ...
    async def put(
        self, bucket: str, key: str, value: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]: ...
    async def command(self, key: str, request: object, action: Command) -> dict[str, Any]: ...
