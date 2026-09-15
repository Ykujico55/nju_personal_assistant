from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from personal_assistant.domain import TaskRun, utc_now


@dataclass(frozen=True, slots=True)
class FuseThresholds:
    same_call_without_progress: int = 3
    consecutive_errors: int = 5
    no_progress_rounds: int = 8
    active_duration: timedelta = timedelta(hours=24)

    def __post_init__(self) -> None:
        if min(
            self.same_call_without_progress,
            self.consecutive_errors,
            self.no_progress_rounds,
        ) < 1:
            raise ValueError("emergency fuse thresholds may not be disabled")
        if self.active_duration <= timedelta(0):
            raise ValueError("active duration fuse may not be disabled")


class EmergencyFuse:
    def __init__(self, thresholds: FuseThresholds | None = None) -> None:
        self.thresholds = thresholds or FuseThresholds()

    def pause_reason(self, run: TaskRun, *, now: datetime | None = None) -> str | None:
        if run.same_call_without_progress >= self.thresholds.same_call_without_progress:
            return "SAME_CALL_WITHOUT_PROGRESS"
        if run.consecutive_errors >= self.thresholds.consecutive_errors:
            return "CONSECUTIVE_ERRORS"
        if run.no_progress_rounds >= self.thresholds.no_progress_rounds:
            return "NO_PROGRESS_ROUNDS"
        if (now or utc_now()) - run.active_started_at >= self.thresholds.active_duration:
            return "ACTIVE_RUN_DURATION"
        return None

