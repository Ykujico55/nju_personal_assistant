from __future__ import annotations

from collections.abc import Iterable

from .models import Evidence


def trace_provenance(evidence_id: str, evidence: Iterable[Evidence]) -> tuple[str, ...]:
    """Return a cycle-safe, depth-first chain of upstream evidence ids."""

    lookup = {item.id: item for item in evidence}
    result: list[str] = []
    visited: set[str] = set()

    def visit(current: str) -> None:
        if current in visited:
            return
        visited.add(current)
        result.append(current)
        item = lookup.get(current)
        if item is not None:
            for parent in item.derived_from:
                visit(parent)

    visit(evidence_id)
    return tuple(result)

