from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from personal_assistant.core.jobs.scheduler import (
    IntervalSchedule,
    MisfirePolicy,
    missed_fire_times,
)


class SchedulerTests(unittest.TestCase):
    def test_offline_catch_up_is_bounded(self) -> None:
        start = datetime(2026, 9, 16, tzinfo=UTC)
        schedule = IntervalSchedule(
            id="mail.poll",
            extension_id="nju.smail",
            interval=timedelta(minutes=5),
            timezone="Asia/Shanghai",
            misfire_policy=MisfirePolicy.CATCH_UP,
            max_catch_up=2,
        )
        due = missed_fire_times(schedule, last_fire_at=start, now=start + timedelta(hours=1))
        self.assertEqual(2, len(due))
        self.assertEqual(start + timedelta(hours=1), due[-1])

    def test_skip_never_replays_after_offline_period(self) -> None:
        start = datetime(2026, 9, 16, tzinfo=UTC)
        schedule = IntervalSchedule(
            id="external.send",
            extension_id="example.external",
            interval=timedelta(minutes=5),
            timezone="UTC",
            misfire_policy=MisfirePolicy.SKIP,
        )
        self.assertEqual(
            (),
            missed_fire_times(
                schedule, last_fire_at=start, now=start + timedelta(hours=1)
            ),
        )


if __name__ == "__main__":
    unittest.main()

