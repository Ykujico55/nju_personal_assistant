"""First-failure metadata; never changes a conversation receipt or its CAS version."""
from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any

from personal_assistant.core.audit import AuditEvent
from personal_assistant.core.conversation_diagnostics import parse_diagnostic
from personal_assistant.core.models.errors import (
    ModelProviderProtocolError,
    ModelProviderResponseTooLargeError,
    ModelProviderTimeoutError,
    ModelProviderUnavailableError,
)
from personal_assistant.domain import ConcurrentModificationError

if TYPE_CHECKING:
    from personal_assistant.bootstrap import Container

BUCKET = "workbench-conversation-diagnostics"
_DIAGNOSTIC_BUDGET = 1.0


class ConversationDiagnostics:
    def __init__(self, container: Container, key: str, request_id: object, actor: str) -> None:
        self.container, self.key, self.actor = container, key, actor
        self.request_id = request_id if (
            isinstance(request_id, str) and re.fullmatch(r"req_[0-9a-f]{32}", request_id)
        ) else None
        self.started = time.monotonic()
        self.stage = "ROUTE"
        self.total_timeout = False

    async def load(self) -> dict[str, Any] | None:
        try:
            async with asyncio.timeout(_DIAGNOSTIC_BUDGET):
                record = await self.container.records.get(BUCKET, self.key)
            diagnosis = parse_diagnostic(record["value"]) if record else None
            # Only confirmed writes are stored; do not upgrade a corrupt record.
            return diagnosis if diagnosis and diagnosis["persisted"] else None
        except Exception:
            return None

    async def record(
        self, error: BaseException | None = None, *, reason_code: str | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        if reason_code is None:
            if isinstance(error, ModelProviderTimeoutError):
                reason_code = "MODEL_TIMEOUT"
            elif isinstance(error, ModelProviderUnavailableError):
                reason_code = "MODEL_CONNECTION_FAILED"
            elif isinstance(error, ModelProviderResponseTooLargeError):
                reason_code = "MODEL_RESPONSE_TOO_LARGE"
            elif isinstance(error, ModelProviderProtocolError):
                reason_code = "MODEL_STREAM_INVALID"
            else:
                reason_code = "INTERNAL_FAILURE"
        diagnosis = {
            "reason_code": reason_code, "stage": stage or self.stage,
            "request_id": self.request_id,
            "elapsed_ms": min(86_400_000, max(0, int((time.monotonic() - self.started) * 1000))),
            "persisted": True,
        }
        assert parse_diagnostic(diagnosis) is not None
        stored = False
        try:
            async with asyncio.timeout(_DIAGNOSTIC_BUDGET):
                try:
                    await self.container.records.put(
                        BUCKET, self.key, diagnosis, expected_version=0
                    )
                    stored = True
                except ConcurrentModificationError:
                    previous = await self.container.records.get(BUCKET, self.key)
                    parsed = parse_diagnostic(previous["value"]) if previous else None
                    if parsed and parsed["persisted"]:
                        return parsed
                if stored:
                    await self.container.audit_writer.append(AuditEvent(
                        event_type="conversation.failed", actor=self.actor,
                        resource_type="conversation", resource_id=self.key, data=diagnosis,
                    ))
        except Exception:
            # No exception text, prompts, vendor payloads or credentials are retained.
            pass
        return {**diagnosis, "persisted": stored}
