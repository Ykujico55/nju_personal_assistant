"""Token statistics contain only known provider counters, never guessed totals."""

from datetime import UTC, datetime

import pytest

from personal_assistant.core.audit import AuditEvent
from personal_assistant.core.audit.usage import summarize_usage
from personal_assistant.infrastructure.memory.audit import InMemoryAuditWriter


def test_known_partial_and_missing_usage_are_separate():
    result = summarize_usage(
        [
            {"usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}},
            {"usage": {"prompt_tokens": 2}},
            {"usage": {}},
            {"usage": {"prompt_tokens": True, "completion_tokens": -2, "total_tokens": "999"}},
        ]
    )
    assert result["calls"] == 4
    assert result["prompt_tokens"] == 6
    assert result["completion_tokens"] == 3
    assert result["total_tokens"] == 7
    assert result["missing_usage_calls"] == 3
    assert result["partial"] is True


def test_absent_usage_is_unknown_not_zero():
    result = summarize_usage([{"usage": {}}, {}])
    assert result["total_tokens"] is None
    assert result["prompt_tokens"] is None
    assert result["missing_usage_calls"] == 2


def test_real_zero_is_valid_and_not_unknown():
    result = summarize_usage(
        [{"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}]
    )
    assert result["total_tokens"] == 0
    assert not result["partial"]


@pytest.mark.asyncio
async def test_memory_reader_only_counts_model_metadata_and_respects_bound():
    writer = InMemoryAuditWriter()
    for kind in ("tool.completed", "model.completed", "model.completed"):
        await writer.append(
            AuditEvent(
                kind,
                "owner",
                "model_call",
                "fixture",
                {
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                    "private_body": "not-for-view",
                },
                datetime.now(UTC),
            )
        )
    result = await writer.model_usage(limit=1)
    assert result["calls"] == 1 and result["total_tokens"] == 3
    assert result["truncated"] is True
    assert "not-for-view" not in str(result)
