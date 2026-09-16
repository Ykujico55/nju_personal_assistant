"""Shared helpers for PostgreSQL adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import overload

from personal_assistant.domain import ValidationError


@overload
def aware_utc(value: datetime) -> datetime: ...


@overload
def aware_utc(value: None) -> None: ...


def aware_utc(value: datetime | None) -> datetime | None:
    """Guarantee timezone-aware UTC datetimes when reading from PostgreSQL."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
