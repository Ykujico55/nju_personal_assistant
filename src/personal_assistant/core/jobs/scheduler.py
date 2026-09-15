from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class MisfirePolicy(StrEnum):
    SKIP = "skip"
    COALESCE = "coalesce"
    CATCH_UP = "catch_up"


@dataclass(frozen=True, slots=True)
class IntervalSchedule:
    id: str
    extension_id: str
    interval: timedelta
    timezone: str
    misfire_policy: MisfirePolicy
    max_catch_up: int = 1

    def __post_init__(self) -> None:
        if self.interval <= timedelta(0):
            raise ValueError("interval must be positive")
        if self.max_catch_up < 0:
            raise ValueError("max_catch_up cannot be negative")


def missed_fire_times(
    schedule: IntervalSchedule,
    *,
    last_fire_at: datetime,
    now: datetime,
) -> tuple[datetime, ...]:
    if now <= last_fire_at:
        return ()
    elapsed = now - last_fire_at
    count = int(elapsed // schedule.interval)
    if count < 1:
        return ()
    due = tuple(last_fire_at + schedule.interval * index for index in range(1, count + 1))
    if schedule.misfire_policy is MisfirePolicy.SKIP:
        return ()
    if schedule.misfire_policy is MisfirePolicy.COALESCE:
        return (due[-1],)
    return due[-schedule.max_catch_up :] if schedule.max_catch_up else ()

