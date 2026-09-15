from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from personal_assistant.domain import TaskRun, ToolCall, ToolOutcome, ToolOutcomeKind, utc_now


def stable_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ProgressDetector:
    def record(self, run: TaskRun, call: ToolCall, outcome: ToolOutcome) -> TaskRun:
        call_fingerprint = stable_fingerprint(
            {"tool": call.tool_id, "version": call.tool_version, "arguments": call.arguments}
        )
        observation = stable_fingerprint(outcome.result) if outcome.result is not None else None
        progressed = (
            outcome.kind is ToolOutcomeKind.SUCCESS
            and observation is not None
            and observation != run.last_observation_fingerprint
        )
        if progressed:
            same_without_progress = 0
        elif call_fingerprint == run.last_call_fingerprint:
            same_without_progress = run.same_call_without_progress + 1
        else:
            same_without_progress = 1
        failed = outcome.kind is ToolOutcomeKind.FAILED
        return replace(
            run,
            version=run.version + 1,
            updated_at=utc_now(),
            last_call_fingerprint=call_fingerprint,
            last_observation_fingerprint=(
                observation if progressed else run.last_observation_fingerprint
            ),
            same_call_without_progress=same_without_progress,
            no_progress_rounds=0 if progressed else run.no_progress_rounds + 1,
            consecutive_errors=run.consecutive_errors + 1 if failed else 0,
        )
