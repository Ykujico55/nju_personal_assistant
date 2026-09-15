from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from personal_assistant.domain import ConcurrentModificationError, TaskRun


class RunRepositoryPort(Protocol):
    async def get(self, run_id: str) -> TaskRun: ...

    async def save(self, run: TaskRun, *, expected_version: int) -> None: ...


class CheckpointStorePort(Protocol):
    async def load(self, run_id: str) -> dict[str, Any]: ...

    async def save(self, run: TaskRun, snapshot: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class Observation:
    run_id: str
    value: Any


class ObservationStorePort(Protocol):
    async def append(self, observation: Observation) -> None: ...


class InMemoryRunRepository:
    def __init__(self, runs: tuple[TaskRun, ...] = ()) -> None:
        self._runs = {run.id: run for run in runs}
        self._lock = asyncio.Lock()

    async def add(self, run: TaskRun) -> None:
        async with self._lock:
            if run.id in self._runs:
                raise ValueError(f"run already exists: {run.id}")
            self._runs[run.id] = deepcopy(run)

    async def get(self, run_id: str) -> TaskRun:
        async with self._lock:
            try:
                return deepcopy(self._runs[run_id])
            except KeyError:
                raise KeyError(f"unknown run: {run_id}") from None

    async def save(self, run: TaskRun, *, expected_version: int) -> None:
        async with self._lock:
            current = self._runs.get(run.id)
            if current is None:
                raise KeyError(run.id)
            if current.version != expected_version or run.version <= expected_version:
                raise ConcurrentModificationError(
                    f"run version changed: expected {expected_version}, got {current.version}"
                )
            self._runs[run.id] = deepcopy(run)


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._snapshots: dict[str, dict[str, Any]] = {}

    async def load(self, run_id: str) -> dict[str, Any]:
        return deepcopy(self._snapshots.get(run_id, {}))

    async def save(self, run: TaskRun, snapshot: dict[str, Any]) -> None:
        self._snapshots[run.id] = {**deepcopy(snapshot), "run_version": run.version}


class InMemoryObservationStore:
    def __init__(self) -> None:
        self.items: list[Observation] = []

    async def append(self, observation: Observation) -> None:
        self.items.append(deepcopy(observation))

