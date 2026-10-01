"""In-memory F08.3 draft store for local/API tests."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from personal_assistant.core.tasks.form_drafts import FormDraft
from personal_assistant.domain import ConcurrentModificationError, NotFoundError


class InMemoryFormDraftStore:
    def __init__(self) -> None:
        self._drafts: dict[str, FormDraft] = {}
        self._commands: dict[str, tuple[str, FormDraft]] = {}
        self._lock = asyncio.Lock()

    async def get(self, task_id: str) -> FormDraft:
        async with self._lock:
            if task_id not in self._drafts:
                raise NotFoundError(f"form draft not found for task {task_id}")
            return deepcopy(self._drafts[task_id])

    async def command_receipt(self, key: str) -> tuple[str, FormDraft] | None:
        async with self._lock:
            prior = self._commands.get(key)
            return deepcopy(prior) if prior is not None else None

    def _replay(self, key: str, fingerprint: str) -> FormDraft | None:
        prior = self._commands.get(key)
        if prior is None:
            return None
        if prior[0] != fingerprint:
            raise ConcurrentModificationError("draft idempotency key was reused with new content")
        return deepcopy(prior[1])

    async def create(self, draft: FormDraft, *, key: str, fingerprint: str) -> FormDraft:
        async with self._lock:
            replay = self._replay(key, fingerprint)
            if replay is not None:
                return replay
            if draft.task_id in self._drafts:
                raise ConcurrentModificationError("task already has a form draft")
            self._drafts[draft.task_id] = deepcopy(draft)
            self._commands[key] = (fingerprint, deepcopy(draft))
            return deepcopy(draft)

    async def replace(
        self, task_id: str, values: dict[str, Any], *, version: int, key: str, fingerprint: str
    ) -> FormDraft:
        async with self._lock:
            replay = self._replay(key, fingerprint)
            if replay is not None:
                return replay
            current = self._drafts.get(task_id)
            if current is None:
                raise NotFoundError(f"form draft not found for task {task_id}")
            if current.version != version:
                raise ConcurrentModificationError("form draft version changed")
            sources = {
                name: (
                    current.sources.get(name, "UNKNOWN")
                    if (
                        name in values
                        and name in current.values
                        and current.values[name] == values[name]
                    )
                    else "USER_INPUT" if name in values else "UNKNOWN"
                )
                for name in current.json_schema["properties"]
            }
            saved = replace(
                current,
                values=deepcopy(values),
                sources=sources,
                version=version + 1,
                updated_at=datetime.now(UTC),
            )
            self._drafts[task_id] = deepcopy(saved)
            self._commands[key] = (fingerprint, deepcopy(saved))
            return deepcopy(saved)
