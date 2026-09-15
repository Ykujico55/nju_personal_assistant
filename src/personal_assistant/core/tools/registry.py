"""Immutable, version-exact tool registry snapshots."""

from __future__ import annotations

import copy
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from personal_assistant.domain.errors import AlreadyExistsError, NotFoundError
from personal_assistant.domain.models import ToolDescriptor


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return copy.deepcopy(value)


def _key(tool_id: str, version: str) -> tuple[str, str]:
    return tool_id, version


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    generation: int
    tools: Mapping[tuple[str, str], ToolDescriptor]

    def resolve(self, tool_id: str, version: str) -> ToolDescriptor:
        try:
            return self.tools[_key(tool_id, version)]
        except KeyError as exc:
            raise NotFoundError(f"tool not registered: {tool_id}@{version}") from exc

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return tuple(self.tools.values())


class ToolRegistry:
    """Publishes all enabled extension capabilities as one atomic snapshot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = RegistrySnapshot(0, MappingProxyType({}))

    def snapshot(self) -> RegistrySnapshot:
        return self._snapshot

    def publish(self, descriptors: Iterable[ToolDescriptor]) -> RegistrySnapshot:
        staged: dict[tuple[str, str], ToolDescriptor] = {}
        for descriptor in descriptors:
            key = _key(descriptor.id, descriptor.version)
            if key in staged:
                raise AlreadyExistsError(
                    f"duplicate tool descriptor: {descriptor.id}@{descriptor.version}"
                )
            staged[key] = replace(
                descriptor,
                input_schema=_freeze(descriptor.input_schema),
                output_schema=_freeze(descriptor.output_schema),
                required_capabilities=frozenset(descriptor.required_capabilities),
                data_classes_in=frozenset(descriptor.data_classes_in),
                data_classes_out=frozenset(descriptor.data_classes_out),
            )
        with self._lock:
            self._snapshot = RegistrySnapshot(
                self._snapshot.generation + 1,
                MappingProxyType(staged),
            )
            return self._snapshot
