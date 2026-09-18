"""Deterministic rank fusion for hybrid retrieval.

Reciprocal Rank Fusion with a fixed ``k`` and a lexicographic tie-break on the
stable chunk key ``(source_id, version_id, ordinal)``: identical inputs always
produce identical orderings, regardless of database row order.
"""

from __future__ import annotations

from collections.abc import Sequence

RRF_K = 60
ChunkKey = tuple[str, str, int]


def rrf_fuse(
    rankings: Sequence[Sequence[ChunkKey]], *, k: int = RRF_K
) -> list[tuple[ChunkKey, float]]:
    scores: dict[ChunkKey, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def dedupe(keys: Sequence[ChunkKey]) -> list[ChunkKey]:
    seen: set[ChunkKey] = set()
    ordered: list[ChunkKey] = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


__all__ = ["RRF_K", "ChunkKey", "dedupe", "rrf_fuse"]
