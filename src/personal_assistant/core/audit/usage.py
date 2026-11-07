"""Metadata-only model usage; unavailable provider counts remain unknown."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

MAX_USAGE_CALLS = 5000


@runtime_checkable
class ModelUsageReaderPort(Protocol):
    async def model_usage(self, *, limit: int = MAX_USAGE_CALLS) -> dict[str, Any]: ...


def summarize_usage(
    rows: Iterable[Mapping[str, Any]],
    *,
    limit: int = MAX_USAGE_CALLS,
) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= MAX_USAGE_CALLS:
        raise ValueError("invalid usage limit")
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    known = dict.fromkeys(totals, 0)
    missing = 0
    count = 0
    truncated = False
    for row in rows:
        if count == limit:
            truncated = True
            break
        count += 1
        usage = row.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        values = {
            "prompt_tokens": usage.get("prompt_tokens", usage.get("prompt_eval_count")),
            "completion_tokens": usage.get("completion_tokens", usage.get("eval_count")),
            "total_tokens": usage.get("total_tokens"),
        }
        complete = True
        for name, value in values.items():
            if type(value) is int and 0 <= value <= 10**12:
                totals[name] += value
                known[name] += 1
            else:
                complete = False
        if not complete:
            missing += 1
    return {
        "scope": "latest_model_calls",
        "limit": limit,
        "calls": count,
        **{name: value if known[name] else None for name, value in totals.items()},
        "known_counts": known,
        "missing_usage_calls": missing,
        "partial": missing > 0,
        "truncated": truncated,
    }
