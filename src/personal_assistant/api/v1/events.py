from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from personal_assistant.api.dependencies import get_container
from personal_assistant.bootstrap import Container

router = APIRouter(tags=["events"])


@router.get("/events", response_class=StreamingResponse)
async def events(
    request: Request,
    after: int = Query(default=0, ge=0),
    once: bool = False,
    container: Container = Depends(get_container),
) -> StreamingResponse:
    header = request.headers.get("Last-Event-ID")
    if header and header.isdigit():
        after = max(after, int(header))

    async def stream() -> AsyncIterator[str]:
        cursor = after
        while True:
            pending = await container.events.after(cursor)
            for event in pending:
                cursor = event.sequence
                data = {
                    "type": event.type,
                    "task_id": event.task_id,
                    "data": event.data,
                    "occurred_at": event.occurred_at.isoformat(),
                }
                yield (
                    f"id: {event.sequence}\n"
                    f"event: {event.type}\n"
                    f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                )
            if once or await request.is_disconnected():
                return
            wait_after = getattr(container.events, "wait_after", None)
            if wait_after is None:
                yield ": keepalive\n\n"
                return
            awaited = await wait_after(cursor, 15.0)
            if not awaited:
                yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )

