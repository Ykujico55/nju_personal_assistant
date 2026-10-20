"""Canonical event collection, cursor commit and dedupe before workflow delivery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from personal_assistant.core.records import RecordStore, fingerprint
from personal_assistant.domain import ValidationError

Poll = Callable[[str, str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
Sink = Callable[[dict[str, Any], str], Awaitable[None]]


class EventCollector:
    def __init__(self, records: RecordStore, poll: Poll, *, sink: Sink | None = None) -> None:
        self.records, self.poll, self.sink = records, poll, sink

    async def collect(self, payload: dict[str, Any], *, job_id: str) -> dict[str, Any]:
        if set(payload) != {"extension_id", "extension_version", "source_id"} or not all(
            isinstance(v, str) and 0 < len(v) <= 128 for v in payload.values()
        ):
            raise ValidationError("invalid event source binding")
        source = payload["extension_id"] + ":" + payload["source_id"]

        async def apply() -> dict[str, Any]:
            old = await self.records.get("event-cursors", source)
            cursor = old["value"]["cursor"] if old else None
            batch = await self.poll(
                payload["extension_id"],
                payload["extension_version"],
                {
                    "source_id": payload["source_id"],
                    "cursor": cursor,
                    "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                    "limit": 100,
                },
            )
            events, keys, next_cursor = (
                batch.get("events"),
                batch.get("source_dedupe_keys"),
                batch.get("next_cursor"),
            )
            if (
                not isinstance(events, list)
                or len(events) > 100
                or not isinstance(keys, list)
                or not all(isinstance(k, str) and 0 < len(k) <= 256 for k in keys)
                or len(set(keys)) != len(keys)
                or not (
                    next_cursor is None
                    or isinstance(next_cursor, str)
                    and len(next_cursor) <= 10_000
                )
            ):
                raise ValidationError("event batch is incomplete")
            event_keys = []
            for event in events:
                if (
                    not isinstance(event, dict)
                    or set(event) != {"type", "source_dedupe_key", "occurred_at", "payload"}
                    or not isinstance(event["type"], str)
                    or not 0 < len(event["type"]) <= 128
                    or not isinstance(event["source_dedupe_key"], str)
                    or not isinstance(event["occurred_at"], str)
                    or not isinstance(event["payload"], dict)
                ):
                    raise ValidationError("domain event is incomplete")
                try:
                    when = datetime.fromisoformat(event["occurred_at"])
                    if when.tzinfo is None:
                        raise ValueError()
                except ValueError:
                    raise ValidationError("event time must be an aware ISO timestamp") from None
                fingerprint(event)
                event_keys.append(event["source_dedupe_key"])
            if len(set(event_keys)) != len(event_keys) or set(event_keys) != set(keys):
                raise ValidationError("event dedupe keys do not match the batch")
            added = 0
            for event in events:
                identity = fingerprint({"source": source, "dedupe": event["source_dedupe_key"]})
                existing = await self.records.get("domain-events", identity)
                if existing:
                    if existing["value"]["event"] != event:
                        raise ValidationError("immutable domain event changed")
                    continue
                await self.records.put(
                    "domain-events",
                    identity,
                    {
                        "source": source,
                        "extension_version": payload["extension_version"],
                        "event": event,
                    },
                    expected_version=0,
                )
                if self.sink is not None:
                    await self.sink({**event, **payload}, identity)
                added += 1
            await self.records.put(
                "event-cursors",
                source,
                {"cursor": next_cursor, "extension_version": payload["extension_version"]},
                expected_version=old["version"] if old else 0,
            )
            return {"added": added, "source": source}

        return await self.records.command("poll-job:" + job_id, payload, apply)
