"""In-memory knowledge store double for unit tests (not a production adapter)."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any


class FakeKnowledgeStore:
    def __init__(self) -> None:
        self.sources: dict[str, dict[str, Any]] = {}
        self.versions: dict[tuple[str, str], dict[str, Any]] = {}
        self.chunks: dict[tuple[str, str, int], dict[str, Any]] = {}
        self.tombstones: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.event_keys: set[str] = set()
        self.meta: dict[str, Any] = {}
        self.migrations_applied: list[int] = []
        self.fail_activation = False
        self.vector_identities: list[Any] = []

    async def migrate(self, migrations: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        self.migrations_applied = [int(item["version"]) for item in migrations]
        return {"applied": self.migrations_applied, "skipped": []}

    async def fetch_all_sources(self) -> dict[str, Any]:
        from personal_knowledge.models import SourceRecord

        return {
            source_id: SourceRecord(**{
                key: row[key]
                for key in (
                    "source_id",
                    "root_key",
                    "relative_path",
                    "media_type",
                    "size_bytes",
                    "mtime_ns",
                    "content_hash",
                    "active_version",
                    "generation",
                )
            })
            for source_id, row in self.sources.items()
        }

    async def fetch_sources(self, root_keys: Sequence[str]) -> dict[str, Any]:
        from personal_knowledge.models import SourceRecord

        return {
            source_id: SourceRecord(**{
                key: row[key]
                for key in (
                    "source_id",
                    "root_key",
                    "relative_path",
                    "media_type",
                    "size_bytes",
                    "mtime_ns",
                    "content_hash",
                    "active_version",
                    "generation",
                )
            })
            for source_id, row in self.sources.items()
            if row["root_key"] in set(root_keys)
        }

    async def get_source(self, source_id: str) -> Any:
        rows = await self.fetch_all_sources()
        return rows.get(source_id)

    async def source_identity_generation(self, source_id: str) -> tuple[str, int] | None:
        source = self.sources.get(source_id)
        if source is not None:
            return "active", int(source["generation"])
        tombstone = self.tombstones.get(source_id)
        if tombstone is not None:
            return "tombstone", int(tombstone["generation"])
        return None

    async def active_version_identity(self, source_id: str) -> dict[str, Any] | None:
        row = self.sources.get(source_id)
        if row is None or not row.get("active_version"):
            return None
        version = self.versions.get((source_id, row["active_version"]))
        if version is None:
            return None
        return {
            "version_id": version["version_id"],
            "content_hash": version["content_hash"],
            "extractor_name": version["extractor_name"],
            "extractor_version": version["extractor_version"],
            "embedding_provider": version["embedding_provider"],
            "embedding_model": version["embedding_model"],
            "embedding_dim": version["embedding_dim"],
            "embedding_version": version["embedding_version"],
        }

    async def insert_source(self, **kwargs: Any) -> Any:
        for row in self.sources.values():
            if (
                row["root_key"] == kwargs["root_key"]
                and row["relative_path"] == kwargs["relative_path"]
            ):
                return await self.get_source(row["source_id"]), False
        if kwargs["source_id"] in self.sources:
            return None
        previous = self.tombstones.get(kwargs["source_id"])
        self.sources[kwargs["source_id"]] = dict(
            kwargs,
            active_version=None,
            generation=int(previous.get("generation", 0)) if previous else 0,
        )
        return await self.get_source(kwargs["source_id"]), True

    async def move_source(self, **kwargs: Any) -> int | None:
        row = self.sources[kwargs["source_id"]]
        if (
            row["root_key"] != kwargs["expected_root_key"]
            or row["relative_path"] != kwargs["expected_relative_path"]
            or row["generation"] != kwargs["expected_generation"]
        ):
            return None
        row["root_key"] = kwargs["root_key"]
        row["relative_path"] = kwargs["relative_path"]
        row["media_type"] = kwargs["media_type"]
        row["size_bytes"] = kwargs["size_bytes"]
        row["mtime_ns"] = kwargs["mtime_ns"]
        row["generation"] += 1
        return int(row["generation"])

    async def touch_source(self, **kwargs: Any) -> None:
        row = self.sources.get(kwargs["source_id"])
        if row is not None:
            row["size_bytes"] = kwargs["size_bytes"]
            row["mtime_ns"] = kwargs["mtime_ns"]

    async def begin_version(self, **kwargs: Any) -> bool:
        key = (kwargs["source_id"], kwargs["version_id"])
        if key in self.versions:
            return False
        self.versions[key] = dict(
            kwargs, state="BUILDING", chunk_count=0, heartbeat_at=time.monotonic()
        )
        return True

    async def heartbeat_version(self, **kwargs: Any) -> bool:
        version = self.versions.get((kwargs["source_id"], kwargs["version_id"]))
        if (
            version is None
            or version["state"] != "BUILDING"
            or version.get("built_by") != kwargs["built_by"]
        ):
            return False
        version["heartbeat_at"] = time.monotonic()
        return True

    async def insert_chunks(
        self,
        *,
        source_id: str,
        version_id: str,
        chunks: Sequence[Any],
        embeddings: Sequence[Any],
        built_by: str,
    ) -> None:
        version = self.versions.get((source_id, version_id))
        if (
            version is None
            or version["state"] != "BUILDING"
            or version.get("built_by") != built_by
        ):
            return
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            self.chunks[(source_id, version_id, chunk.ordinal)] = {
                "text": chunk.text,
                "locator": chunk.locator.as_json(),
                "content_hash": chunk.content_hash,
                "embedding": embedding,
            }
        await self.heartbeat_version(
            source_id=source_id, version_id=version_id, built_by=built_by
        )

    async def activate_version(self, **kwargs: Any) -> int:
        if self.fail_activation:
            raise RuntimeError("activation failed")
        source_id = kwargs["source_id"]
        version_id = kwargs["version_id"]
        candidate = self.versions.get((source_id, version_id))
        if source_id not in self.sources or candidate is None:
            raise RuntimeError("activation failed: candidate is no longer buildable")
        if candidate["state"] != "BUILDING":
            raise RuntimeError("activation failed: candidate is no longer buildable")
        if candidate.get("built_by") != kwargs.get("built_by"):
            raise RuntimeError("activation failed: candidate ownership changed")
        expected = kwargs.get("expected_active_version")
        if self.sources[source_id].get("active_version") != expected:
            raise RuntimeError("activation failed: the active version changed (CAS)")
        source = self.sources[source_id]
        if (
            source["root_key"] != kwargs["expected_root_key"]
            or source["relative_path"] != kwargs["expected_relative_path"]
            or source["generation"] != kwargs["expected_generation"]
        ):
            raise RuntimeError("activation failed: the source snapshot changed (CAS)")
        candidate["state"] = "READY"
        candidate["chunk_count"] = kwargs["chunk_count"]
        candidate["built_by"] = None
        row = self.sources[source_id]
        row["active_version"] = version_id
        row["content_hash"] = kwargs["content_hash"]
        row["size_bytes"] = kwargs["size_bytes"]
        row["mtime_ns"] = kwargs["mtime_ns"]
        row["generation"] += 1
        # Only superseded READY versions are removed; BUILDING candidates of
        # other in-flight builds survive.
        for key in [
            key
            for key in self.versions
            if key[0] == source_id
            and key[1] != version_id
            and self.versions[key]["state"] == "READY"
        ]:
            self.versions.pop(key)
            for chunk_key in [
                chunk_key
                for chunk_key in self.chunks
                if chunk_key[0] == source_id and chunk_key[1] == key[1]
            ]:
                self.chunks.pop(chunk_key)
        return int(row["generation"])

    async def fail_version(
        self, *, source_id: str, version_id: str, built_by: str
    ) -> None:
        version = self.versions.get((source_id, version_id))
        if (
            version is None
            or version["state"] != "BUILDING"
            or version.get("built_by") != built_by
        ):
            return
        self.versions.pop((source_id, version_id), None)
        for key in [
            key for key in self.chunks if key[0] == source_id and key[1] == version_id
        ]:
            self.chunks.pop(key)

    async def discard_orphan_building_versions(
        self, *, run_id: str, max_age_seconds: int = 3600
    ) -> None:
        cutoff = time.monotonic() - max_age_seconds
        for key in [
            key
            for key, row in self.versions.items()
            if row["state"] == "BUILDING"
            and row.get("built_by") not in (None, run_id)
            and float(row.get("heartbeat_at", 0.0)) < cutoff
        ]:
            self.versions.pop(key)
            for chunk_key in [
                chunk_key
                for chunk_key in self.chunks
                if chunk_key[0] == key[0] and chunk_key[1] == key[1]
            ]:
                self.chunks.pop(chunk_key)

    async def delete_source(self, **kwargs: Any) -> int | None:
        source_id = kwargs["source_id"]
        row = self.sources.get(source_id)
        if row is None or (
            row["root_key"] != kwargs["expected_root_key"]
            or row["relative_path"] != kwargs["expected_relative_path"]
            or row["generation"] != kwargs["expected_generation"]
        ):
            return None
        row = self.sources.pop(source_id)
        generation = int(row["generation"]) + 1
        self.tombstones[source_id] = {
            "root_key": row["root_key"],
            "relative_path": row["relative_path"],
            "last_content_hash": kwargs["reason_hash"],
            "generation": generation,
        }
        for key in [key for key in self.versions if key[0] == source_id]:
            self.versions.pop(key)
        for key in [key for key in self.chunks if key[0] == source_id]:
            self.chunks.pop(key)
        return generation

    async def append_event(self, **kwargs: Any) -> bool:
        dedupe = kwargs["dedupe_key"]
        if dedupe in self.event_keys:
            return False
        self.event_keys.add(dedupe)
        self.events.append(dict(kwargs, sequence=len(self.events) + 1))
        return True

    async def fetch_events(self, *, after: int, limit: int) -> list[dict[str, Any]]:
        return [row for row in self.events if row["sequence"] > after][:limit]

    async def set_meta(self, key: str, value: Mapping[str, Any]) -> None:
        self.meta[key] = dict(value)

    async def get_meta(self, key: str) -> dict[str, Any] | None:
        value = self.meta.get(key)
        return dict(value) if isinstance(value, Mapping) else None

    async def fetch_chunks(
        self, keys: Sequence[tuple[str, str, int]]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key in keys:
            chunk = self.chunks.get(key)
            if chunk is None:
                continue
            source = next(
                (row for row in self.sources.values() if row["source_id"] == key[0]), None
            )
            if source is None or source["active_version"] != key[1]:
                continue
            rows.append(
                {
                    "source_id": key[0],
                    "version_id": key[1],
                    "ordinal": key[2],
                    "text": chunk["text"],
                    "locator": json.dumps(chunk["locator"]),
                    "heading_path": "",
                    "content_hash": chunk["content_hash"],
                    "root_key": source["root_key"],
                    "relative_path": source["relative_path"],
                    "media_type": source["media_type"],
                    "source_hash": source["content_hash"],
                }
            )
        return rows

    async def search_fts(
        self, *, query: str, limit: int, filters: Mapping[str, Sequence[str]]
    ) -> list[dict[str, Any]]:
        terms = [term.lower() for term in query.split() if term]
        scored: list[tuple[float, dict[str, Any]]] = []
        for key, chunk in self.chunks.items():
            source = next(
                (row for row in self.sources.values() if row["source_id"] == key[0]), None
            )
            if source is None or source["active_version"] != key[1]:
                continue
            if not _passes_filters(source, filters):
                continue
            text = chunk["text"].lower()
            score = sum(1.0 for term in terms if term in text)
            if score == 0:
                continue
            scored.append(
                (
                    score,
                    {
                        "source_id": key[0],
                        "version_id": key[1],
                        "ordinal": key[2],
                    },
                )
            )
        scored.sort(
            key=lambda item: (
                -item[0],
                item[1]["source_id"],
                item[1]["version_id"],
                item[1]["ordinal"],
            )
        )
        return [row for _score, row in scored[:limit]]

    async def search_vector(
        self,
        *,
        vector: Sequence[float],
        limit: int,
        filters: Mapping[str, Sequence[str]],
        identity: Any = None,
    ) -> list[dict[str, Any]]:
        del vector, limit, filters
        self.vector_identities.append(identity)
        return []


def _passes_filters(source: Mapping[str, Any], filters: Mapping[str, Sequence[str]]) -> bool:
    for name, column in (
        ("root_keys", "root_key"),
        ("media_types", "media_type"),
        ("source_ids", "source_id"),
    ):
        values = filters.get(name)
        if values and source[column] not in set(values):
            return False
    return True


__all__ = ["FakeKnowledgeStore"]
