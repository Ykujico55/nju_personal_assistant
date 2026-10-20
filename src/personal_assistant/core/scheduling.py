"""Persistent read-only event scheduling; offline time never queues external actions."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_assistant.core.jobs import JobQueuePort
from personal_assistant.core.records import RecordStore
from personal_assistant.domain import ValidationError


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValidationError("schedule time must be timezone aware")
    return value.astimezone(UTC)


def cron_fields(expression: str) -> tuple[frozenset[int], ...]:
    fields = expression.split()
    bounds = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
    if len(fields) != 5 or len(expression) > 128:
        raise ValidationError("cron must have five numeric fields")
    parsed = []
    for field, (low, high) in zip(fields, bounds, strict=True):
        result: set[int] = set()
        for item in field.split(","):
            base, sep, raw_step = item.partition("/")
            if sep and (not raw_step.isdecimal() or not 1 <= int(raw_step) <= high + 1):
                raise ValidationError("invalid cron step")
            step = int(raw_step) if sep else 1
            if base == "*":
                start, end = low, high
            elif re.fullmatch(r"[0-9]+-[0-9]+", base):
                start, end = map(int, base.split("-"))
            elif base.isdecimal():
                start = end = int(base)
            else:
                raise ValidationError("unsupported cron syntax")
            if not low <= start <= end <= high:
                raise ValidationError("cron range is invalid")
            result.update(range(start, end + 1, step))
        if not result:
            raise ValidationError("empty cron field")
        parsed.append(frozenset(result))
    return tuple(parsed)


def cron_next(expression: str, after: datetime, timezone: str) -> datetime:
    fields = cron_fields(expression)
    zone = ZoneInfo(timezone)
    cursor = _aware(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    # Bound work and explicitly reject impossible/sparse schedules, never reinterpret them.
    for _ in range(527_040):
        local = cursor.astimezone(zone)
        dow = (local.weekday() + 1) % 7
        day = local.day in fields[2]
        weekday = dow in fields[4]
        day = day or weekday if len(fields[2]) != 31 and len(fields[4]) != 7 else day and weekday
        if (
            local.minute in fields[0]
            and local.hour in fields[1]
            and local.month in fields[3]
            and day
        ):
            return cursor
        cursor += timedelta(minutes=1)
    raise ValidationError("cron has no occurrence within the supported one-year horizon")


class DurableScheduler:
    def __init__(self, records: RecordStore, jobs: JobQueuePort) -> None:
        self.records, self.jobs = records, jobs

    async def configure(
        self, definition: dict[str, Any], *, key: str, now: datetime | None = None
    ) -> dict[str, Any]:
        allowed = {
            "id",
            "source_id",
            "extension_id",
            "extension_version",
            "interval_seconds",
            "cron",
            "timezone",
            "misfire_policy",
            "max_catch_up",
        }
        if set(definition) - allowed or not all(
            isinstance(definition.get(k), str)
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", definition[k])
            for k in ("id", "source_id", "extension_id", "extension_version")
        ):
            raise ValidationError("schedule must target a declared read-only event source")
        if definition.get("misfire_policy") not in {"skip", "coalesce", "catch_up"}:
            raise ValidationError("unsupported misfire policy")
        interval, expression = definition.get("interval_seconds"), definition.get("cron")
        if (interval is None) == (expression is None):
            raise ValidationError("choose one interval or cron expression")
        if interval is not None and (type(interval) is not int or not 30 <= interval <= 31_536_000):
            raise ValidationError("interval must be from 30 seconds to one year")
        catch_up = definition.get("max_catch_up", 1)
        if type(catch_up) is not int or not 1 <= catch_up <= 10:
            raise ValidationError("catch-up must be bounded to one to ten polls")
        timezone = definition.get("timezone")
        if not isinstance(timezone, str):
            raise ValidationError("an IANA timezone is required")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError("unknown timezone") from None
        if expression is not None and not isinstance(expression, str):
            raise ValidationError("cron must be a string")
        current = _aware(now or datetime.now(UTC))
        next_at = (
            current + timedelta(seconds=interval)
            if interval is not None
            else cron_next(expression or "", current, timezone)
        )

        async def apply() -> dict[str, Any]:
            old = await self.records.get("schedules", definition["id"])
            if old is not None:
                raise ValidationError("schedule already exists; disable it before replacement")
            return await self.records.put(
                "schedules",
                definition["id"],
                {"definition": definition, "next_at": next_at.isoformat(), "enabled": True},
                expected_version=0,
            )

        return await self.records.command(
            key, {"operation": "schedule", "definition": definition}, apply
        )

    async def tick(self, *, now: datetime | None = None) -> int:
        current = _aware(now or datetime.now(UTC))
        fired = 0
        for record in await self.records.list("schedules"):
            value = record["value"]
            if not value["enabled"]:
                continue
            due = datetime.fromisoformat(value["next_at"])
            if due > current:
                continue
            definition = value["definition"]
            interval = definition.get("interval_seconds")
            occurrences = []
            if interval is not None:
                missed = int((current - due).total_seconds() // interval)
                latest = due + timedelta(seconds=interval * missed)
                next_at = latest + timedelta(seconds=interval)
                count = min(missed + 1, definition.get("max_catch_up", 1))
                occurrences = [
                    latest - timedelta(seconds=interval * i) for i in reversed(range(count))
                ]
            else:
                cursor = due
                for _ in range(1440):
                    if cursor > current:
                        break
                    occurrences.append(cursor)
                    occurrences = occurrences[-definition.get("max_catch_up", 1) :]
                    cursor = cron_next(definition["cron"], cursor, definition["timezone"])
                else:
                    raise ValidationError("too many missed cron periods; explicit review required")
                latest, next_at = occurrences[-1], cursor
            policy = definition["misfire_policy"]
            if policy == "skip" and current - due > timedelta(seconds=60):
                occurrences = []
            elif policy != "catch_up":
                occurrences = [latest]

            async def apply(
                r: dict[str, Any] = record,
                v: dict[str, Any] = value,
                d: dict[str, Any] = definition,
                times: list[datetime] = occurrences,
                next_time: datetime = next_at,
            ) -> dict[str, Any]:
                for time in times:
                    await self.jobs.enqueue(
                        kind="event.poll",
                        payload={
                            "extension_id": d["extension_id"],
                            "extension_version": d["extension_version"],
                            "source_id": d["source_id"],
                        },
                        idempotency_key=f"schedule:{r['id']}:{time.isoformat()}",
                    )
                await self.records.put(
                    "schedules",
                    r["id"],
                    {**v, "next_at": next_time.isoformat()},
                    expected_version=r["version"],
                )
                return {"fired": len(times)}

            result = await self.records.command(
                f"schedule-tick:{record['id']}:{record['version']}",
                {"next_at": next_at.isoformat(), "times": [t.isoformat() for t in occurrences]},
                apply,
            )
            fired += int(result["fired"])
        return fired
