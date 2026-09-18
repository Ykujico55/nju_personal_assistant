"""SQL repository for the knowledge index.

All persistence goes through the host's generic extension data capability; the
extension never sees a connection string or credential.  Every mutation is
idempotent (``ON CONFLICT``) and every activation is a single transaction, so
concurrent or repeated reconciliation converges to exactly one active version
per source.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, cast

from personal_assistant_sdk import HostDataClient

from .embedding import EmbeddingIdentity
from .models import Chunk, ChunkRecord, Locator, SourceRecord

MAX_CHUNK_BATCH = 200
MAX_CHUNK_BATCH_BYTES = 256 * 1024

_CHUNK_SELECT = (
    "SELECT c.source_id, c.version_id, c.ordinal, c.text, c.locator, "
    "c.heading_path, c.content_hash, s.root_key, s.relative_path, s.media_type, "
    "s.content_hash AS source_hash "
    "FROM knowledge_chunks c "
    "JOIN knowledge_sources s "
    "  ON s.source_id = c.source_id AND s.active_version = c.version_id"
)

_INSERT_CHUNK = (
    "INSERT INTO knowledge_chunks ("
    "source_id, version_id, ordinal, text, locator, heading_path, page, "
    "line_start, line_end, char_start, char_end, content_hash, embedding"
    ") SELECT $1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, $12, "
    "$13::vector WHERE EXISTS (SELECT 1 FROM knowledge_versions "
    "WHERE source_id = $1 AND version_id = $2 AND state = 'BUILDING' AND built_by = $14) "
    "ON CONFLICT (source_id, version_id, ordinal) DO UPDATE SET "
    "text = EXCLUDED.text, locator = EXCLUDED.locator, "
    "heading_path = EXCLUDED.heading_path, page = EXCLUDED.page, "
    "line_start = EXCLUDED.line_start, line_end = EXCLUDED.line_end, "
    "char_start = EXCLUDED.char_start, char_end = EXCLUDED.char_end, "
    "content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding"
)


class KnowledgeStore:
    def __init__(self, data: HostDataClient) -> None:
        self._data = data

    # -- migrations ---------------------------------------------------------

    async def migrate(self, migrations: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        return await self._data.migrate(migrations)

    # -- sources ------------------------------------------------------------

    async def fetch_sources(self, root_keys: Sequence[str]) -> dict[str, SourceRecord]:
        statement = (
            "SELECT source_id, root_key, relative_path, media_type, size_bytes, "
            "mtime_ns, content_hash, active_version, generation "
            "FROM knowledge_sources WHERE root_key = ANY($1::text[])"
        )
        rows = await self._rows(statement, [list(root_keys)])
        return {
            str(row["source_id"]): _source_record(row)
            for row in rows
            if row.get("source_id") is not None
        }

    async def fetch_all_sources(self) -> dict[str, SourceRecord]:
        rows = await self._rows(
            "SELECT source_id, root_key, relative_path, media_type, size_bytes, "
            "mtime_ns, content_hash, active_version, generation FROM knowledge_sources"
        )
        return {
            str(row["source_id"]): _source_record(row)
            for row in rows
            if row.get("source_id") is not None
        }

    async def get_source(self, source_id: str) -> SourceRecord | None:
        rows = await self._rows(
            "SELECT source_id, root_key, relative_path, media_type, size_bytes, "
            "mtime_ns, content_hash, active_version, generation "
            "FROM knowledge_sources WHERE source_id = $1",
            [source_id],
        )
        return _source_record(rows[0]) if rows else None

    async def source_identity_generation(self, source_id: str) -> tuple[str, int] | None:
        """Return the live/tombstone generation used for event identity selection."""

        rows = await self._rows(
            "SELECT 'active'::text AS kind, generation FROM knowledge_sources "
            "WHERE source_id = $1 UNION ALL "
            "SELECT 'tombstone'::text AS kind, generation FROM knowledge_tombstones "
            "WHERE source_id = $1 AND NOT EXISTS ("
            "SELECT 1 FROM knowledge_sources WHERE source_id = $1) LIMIT 1",
            [source_id],
        )
        if not rows:
            return None
        return str(rows[0]["kind"]), int(cast(int, rows[0]["generation"]))

    async def insert_source(
        self,
        *,
        source_id: str,
        root_key: str,
        relative_path: str,
        media_type: str,
        size_bytes: int,
        mtime_ns: int,
        content_hash: str,
    ) -> tuple[SourceRecord, bool] | None:
        """Insert a brand-new source without touching an existing row's hash.

        ``knowledge_sources.content_hash`` is only advanced by
        ``activate_version`` so a failed rebuild can never make stale chunks look
        current.
        """

        result = await self._data.execute(
            "INSERT INTO knowledge_sources ("
            "source_id, root_key, relative_path, media_type, size_bytes, mtime_ns, "
            "content_hash, generation) VALUES ($1, $2, $3, $4, $5, $6, $7, "
            "COALESCE((SELECT generation FROM knowledge_tombstones "
            "WHERE source_id = $1), 0)) "
            "ON CONFLICT DO NOTHING",
            [
                source_id,
                root_key,
                relative_path,
                media_type,
                size_bytes,
                mtime_ns,
                content_hash,
            ],
        )
        rows = await self._rows(
            "SELECT source_id, root_key, relative_path, media_type, size_bytes, "
            "mtime_ns, content_hash, active_version, generation "
            "FROM knowledge_sources WHERE root_key = $1 AND relative_path = $2",
            [root_key, relative_path],
        )
        if not rows:
            return None
        return _source_record(rows[0]), int(cast(int, result.get("rowcount", 0))) == 1

    async def move_source(
        self,
        *,
        source_id: str,
        root_key: str,
        relative_path: str,
        media_type: str,
        size_bytes: int,
        mtime_ns: int,
        expected_root_key: str,
        expected_relative_path: str,
        expected_generation: int,
    ) -> int | None:
        result = await self._data.execute(
            "UPDATE knowledge_sources SET root_key = $2, relative_path = $3, "
            "media_type = $4, size_bytes = $5, mtime_ns = $6, "
            "generation = generation + 1, updated_at = now() "
            "WHERE source_id = $1 AND root_key = $7 AND relative_path = $8 "
            "AND generation = $9 RETURNING generation",
            [
                source_id,
                root_key,
                relative_path,
                media_type,
                size_bytes,
                mtime_ns,
                expected_root_key,
                expected_relative_path,
                expected_generation,
            ],
        )
        rows = result.get("rows")
        if not isinstance(rows, list) or not rows:
            return None
        if len(rows) != 1:
            raise RuntimeError("move failed: source update returned multiple rows")
        return int(cast(Mapping[str, Any], rows[0])["generation"])

    async def touch_source(
        self, *, source_id: str, size_bytes: int, mtime_ns: int
    ) -> None:
        await self._data.execute(
            "UPDATE knowledge_sources SET size_bytes = $2, mtime_ns = $3, "
            "updated_at = now() WHERE source_id = $1",
            [source_id, size_bytes, mtime_ns],
        )

    # -- versions and chunks ------------------------------------------------

    async def begin_version(
        self,
        *,
        source_id: str,
        version_id: str,
        content_hash: str,
        extractor_name: str,
        extractor_version: str,
        embedding_provider: str,
        embedding_model: str,
        embedding_dim: int,
        embedding_version: str,
        built_by: str,
    ) -> bool:
        result = await self._data.execute(
            "INSERT INTO knowledge_versions ("
            "source_id, version_id, content_hash, state, extractor_name, "
            "extractor_version, embedding_provider, embedding_model, embedding_dim, "
            "embedding_version, chunk_count, diagnostics, built_by) "
            "VALUES ($1, $2, $3, 'BUILDING', $4, $5, $6, $7, $8, $9, 0, '[]'::jsonb, $10) "
            "ON CONFLICT (source_id, version_id) DO NOTHING",
            [
                source_id,
                version_id,
                content_hash,
                extractor_name,
                extractor_version,
                embedding_provider,
                embedding_model,
                embedding_dim,
                embedding_version,
                built_by,
            ],
        )
        return int(cast(int, result.get("rowcount", 0))) == 1

    async def insert_chunks(
        self,
        *,
        source_id: str,
        version_id: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float] | None],
        built_by: str,
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunk and embedding counts must match")
        batch: list[Mapping[str, Any]] = []
        batch_bytes = 0
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            statement: Mapping[str, Any] = {
                "statement": _INSERT_CHUNK,
                "parameters": [
                    source_id,
                    version_id,
                    chunk.ordinal,
                    chunk.text,
                    json.dumps(chunk.locator.as_json(), sort_keys=True),
                    "\n".join(chunk.heading_path),
                    chunk.locator.page,
                    chunk.locator.start if chunk.locator.kind == "line_range" else None,
                    chunk.locator.end if chunk.locator.kind == "line_range" else None,
                    chunk.locator.start
                    if chunk.locator.kind in {"page_fragment", "document_fragment"}
                    else None,
                    chunk.locator.end
                    if chunk.locator.kind in {"page_fragment", "document_fragment"}
                    else None,
                    chunk.content_hash,
                    _vector_literal(embedding),
                    built_by,
                ],
            }
            item_bytes = len(
                json.dumps(
                    statement,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
            if item_bytes > MAX_CHUNK_BATCH_BYTES:
                raise RuntimeError("one chunk exceeds the bounded data frame budget")
            if batch and (
                len(batch) >= MAX_CHUNK_BATCH
                or batch_bytes + item_bytes > MAX_CHUNK_BATCH_BYTES
            ):
                await self._data.transaction(batch)
                if not await self.heartbeat_version(
                    source_id=source_id, version_id=version_id, built_by=built_by
                ):
                    raise RuntimeError("chunk insertion lost candidate ownership")
                batch = []
                batch_bytes = 0
            batch.append(statement)
            batch_bytes += item_bytes
        if batch:
            await self._data.transaction(batch)
            if not await self.heartbeat_version(
                source_id=source_id, version_id=version_id, built_by=built_by
            ):
                raise RuntimeError("chunk insertion lost candidate ownership")

    async def heartbeat_version(
        self, *, source_id: str, version_id: str, built_by: str
    ) -> bool:
        result = await self._data.execute(
            "UPDATE knowledge_versions SET heartbeat_at = now() "
            "WHERE source_id = $1 AND version_id = $2 "
            "AND state = 'BUILDING' AND built_by = $3",
            [source_id, version_id, built_by],
        )
        return int(cast(int, result.get("rowcount", 0))) == 1

    async def activate_version(
        self,
        *,
        source_id: str,
        version_id: str,
        content_hash: str,
        size_bytes: int,
        mtime_ns: int,
        chunk_count: int,
        diagnostics: Sequence[Mapping[str, Any]],
        expected_active_version: str | None,
        expected_root_key: str,
        expected_relative_path: str,
        expected_generation: int,
        built_by: str,
    ) -> int:
        """Atomically promote one fully built candidate to the active version.

        A per-source row lock serializes activations, and the pointer switch is
        conditional on the previously observed active version (CAS): a build
        that was superseded by a newer one fails closed instead of overwriting
        it.  The source pointer is only advanced when the candidate was promoted
        by the same statement, so it can never reference a deleted row, and only
        superseded READY versions are removed, never another live candidate.
        """

        outcome = await self._data.transaction(
            [
                {
                    "statement": (
                        "WITH eligible AS MATERIALIZED ("
                        "SELECT s.source_id FROM knowledge_sources s "
                        "JOIN knowledge_versions v ON v.source_id = s.source_id "
                        "AND v.version_id = $2 WHERE s.source_id = $1 "
                        "AND s.active_version IS NOT DISTINCT FROM $8 "
                        "AND s.root_key = $9 AND s.relative_path = $10 "
                        "AND s.generation = $11 "
                        "AND v.state = 'BUILDING' AND v.built_by = $12 "
                        "FOR UPDATE OF s, v), promoted AS ("
                        "UPDATE knowledge_versions v SET state = 'READY', "
                        "chunk_count = $3, diagnostics = $4::jsonb, "
                        "completed_at = now(), built_by = NULL "
                        "FROM eligible e WHERE v.source_id = e.source_id "
                        "AND v.version_id = $2 RETURNING v.source_id), switched AS ("
                        "UPDATE knowledge_sources s SET active_version = $2, "
                        "content_hash = $5, size_bytes = $6, mtime_ns = $7, "
                        "generation = s.generation + 1, updated_at = now() "
                        "FROM promoted p WHERE s.source_id = p.source_id "
                        "RETURNING s.source_id, s.generation) "
                        "SELECT source_id, generation FROM switched"
                    ),
                    "parameters": [
                        source_id,
                        version_id,
                        chunk_count,
                        json.dumps(list(diagnostics), sort_keys=True),
                        content_hash,
                        size_bytes,
                        mtime_ns,
                        expected_active_version,
                        expected_root_key,
                        expected_relative_path,
                        expected_generation,
                        built_by,
                    ],
                },
                {
                    "statement": (
                        "DELETE FROM knowledge_versions WHERE source_id = $1 "
                        "AND version_id <> $2 AND state = 'READY' "
                        "AND EXISTS (SELECT 1 FROM knowledge_sources "
                        "WHERE source_id = $1 AND active_version = $2)"
                    ),
                    "parameters": [source_id, version_id],
                },
            ]
        )
        results = outcome.get("results")
        if not isinstance(results, list) or len(results) != 2:
            raise RuntimeError("activation transaction returned an unexpected shape")
        promoted = results[0].get("rows") if isinstance(results[0], Mapping) else None
        if not isinstance(promoted, list) or len(promoted) != 1:
            raise RuntimeError("activation failed: candidate is no longer buildable")
        return int(cast(Mapping[str, Any], promoted[0])["generation"])

    async def fail_version(
        self, *, source_id: str, version_id: str, built_by: str
    ) -> None:
        await self._data.execute(
            "DELETE FROM knowledge_versions WHERE source_id = $1 "
            "AND version_id = $2 AND state = 'BUILDING' AND built_by = $3",
            [source_id, version_id, built_by],
        )

    async def discard_orphan_building_versions(
        self, *, run_id: str, max_age_seconds: int = 3600
    ) -> None:
        """Remove BUILDING candidates left behind by crashed or cancelled runs.

        The current run's own candidates are never touched, and only rows older
        than ``max_age_seconds`` are eligible, so a live concurrent build is safe.
        """

        await self._data.execute(
            "DELETE FROM knowledge_versions WHERE state = 'BUILDING' "
            "AND (built_by IS NULL OR built_by <> $1) "
            "AND heartbeat_at < now() - make_interval(secs => $2)",
            [run_id, int(max_age_seconds)],
        )

    async def delete_source(
        self,
        *,
        source_id: str,
        reason_hash: str,
        expected_root_key: str,
        expected_relative_path: str,
        expected_generation: int,
    ) -> int | None:
        """Delete exactly the source snapshot observed by reconciliation.

        The eligibility lock, content cleanup, tombstone write and source delete
        are one statement.  A stale scan therefore has no side effects and a
        successful deletion consumes its own generation.
        """

        result = await self._data.execute(
            "WITH eligible AS MATERIALIZED ("
            "SELECT source_id, root_key, relative_path, generation "
            "FROM knowledge_sources WHERE source_id = $1 AND root_key = $2 "
            "AND relative_path = $3 AND generation = $4 FOR UPDATE), "
            "removed_chunks AS ("
            "DELETE FROM knowledge_chunks c USING eligible e "
            "WHERE c.source_id = e.source_id RETURNING c.source_id), "
            "removed_versions AS ("
            "DELETE FROM knowledge_versions v USING eligible e "
            "WHERE v.source_id = e.source_id RETURNING v.source_id), "
            "tombstoned AS ("
            "INSERT INTO knowledge_tombstones ("
            "source_id, root_key, relative_path, last_content_hash, generation) "
            "SELECT source_id, root_key, relative_path, $5, generation + 1 "
            "FROM eligible ON CONFLICT (source_id) DO UPDATE SET "
            "root_key = EXCLUDED.root_key, relative_path = EXCLUDED.relative_path, "
            "last_content_hash = EXCLUDED.last_content_hash, "
            "generation = EXCLUDED.generation, deleted_at = now() "
            "RETURNING source_id, generation), deleted AS ("
            "DELETE FROM knowledge_sources s USING eligible e "
            "WHERE s.source_id = e.source_id RETURNING s.source_id) "
            "SELECT t.generation FROM tombstoned t "
            "JOIN deleted d ON d.source_id = t.source_id",
            [
                source_id,
                expected_root_key,
                expected_relative_path,
                expected_generation,
                reason_hash,
            ],
        )
        rows = result.get("rows")
        if not isinstance(rows, list) or not rows:
            return None
        if len(rows) != 1:
            raise RuntimeError("delete failed: source update returned multiple rows")
        return int(cast(Mapping[str, Any], rows[0])["generation"])

    async def active_version_identity(self, source_id: str) -> dict[str, Any] | None:
        rows = await self._rows(
            "SELECT v.version_id, v.content_hash, v.extractor_name, "
            "v.extractor_version, v.embedding_provider, v.embedding_model, "
            "v.embedding_dim, v.embedding_version "
            "FROM knowledge_sources s "
            "JOIN knowledge_versions v "
            "  ON v.source_id = s.source_id AND v.version_id = s.active_version "
            "WHERE s.source_id = $1",
            [source_id],
        )
        if not rows:
            return None
        return dict(rows[0])

    # -- events -------------------------------------------------------------

    async def append_event(
        self,
        *,
        event_type: str,
        source_id: str,
        dedupe_key: str,
        root_key: str,
        relative_path: str,
        content_hash: str | None,
        previous_hash: str | None,
        payload: Mapping[str, Any] | None = None,
    ) -> bool:
        result = await self._data.execute(
            "INSERT INTO knowledge_events ("
            "event_type, source_id, dedupe_key, root_key, relative_path, "
            "content_hash, previous_hash, payload"
            ") VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb) "
            "ON CONFLICT (dedupe_key) DO NOTHING",
            [
                event_type,
                source_id,
                dedupe_key,
                root_key,
                relative_path,
                content_hash,
                previous_hash,
                json.dumps(dict(payload or {}), sort_keys=True),
            ],
        )
        return int(cast(int, result.get("rowcount", 0))) > 0

    async def fetch_events(
        self, *, after: int, limit: int
    ) -> list[dict[str, Any]]:
        rows = await self._rows(
            "SELECT sequence, event_type, source_id, dedupe_key, root_key, "
            "relative_path, content_hash, previous_hash, occurred_at, payload "
            "FROM knowledge_events WHERE sequence > $1 ORDER BY sequence LIMIT $2",
            [after, limit],
        )
        return rows

    async def event_count(self) -> int:
        rows = await self._rows("SELECT count(*) AS total FROM knowledge_events")
        return int(rows[0]["total"]) if rows else 0

    # -- meta ---------------------------------------------------------------

    async def set_meta(self, key: str, value: Mapping[str, Any]) -> None:
        await self._data.execute(
            "INSERT INTO knowledge_meta (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            [key, json.dumps(dict(value), sort_keys=True)],
        )

    async def get_meta(self, key: str) -> dict[str, Any] | None:
        rows = await self._rows(
            "SELECT value FROM knowledge_meta WHERE key = $1", [key]
        )
        if not rows:
            return None
        value = rows[0].get("value")
        return dict(value) if isinstance(value, Mapping) else None

    # -- retrieval ----------------------------------------------------------

    async def search_fts(
        self,
        *,
        query: str,
        limit: int,
        filters: Mapping[str, Sequence[str]],
    ) -> list[dict[str, Any]]:
        clause, parameters = _filter_clause(filters, start=3)
        statement = (
            "SELECT c.source_id, c.version_id, c.ordinal, c.text, c.locator, "
            "c.heading_path, c.content_hash, s.root_key, s.relative_path, "
            "s.media_type, s.content_hash AS source_hash, "
            "ts_rank_cd(c.search_vector, q) AS score "
            "FROM knowledge_chunks c "
            "JOIN knowledge_sources s "
            "  ON s.source_id = c.source_id AND s.active_version = c.version_id "
            "CROSS JOIN plainto_tsquery('simple', $1) AS q "
            f"WHERE c.search_vector @@ q{clause} "
            "ORDER BY score DESC, c.source_id, c.version_id, c.ordinal "
            "LIMIT $2"
        )
        return await self._rows(statement, [query, limit, *parameters])

    async def search_vector(
        self,
        *,
        vector: Sequence[float],
        limit: int,
        filters: Mapping[str, Sequence[str]],
        identity: EmbeddingIdentity,
    ) -> list[dict[str, Any]]:
        clause, parameters = _filter_clause(filters, start=7)
        statement = (
            "WITH matching AS MATERIALIZED ("
            "SELECT c.source_id, c.version_id, c.ordinal, c.text, c.locator, "
            "c.heading_path, c.content_hash, c.embedding, s.root_key, "
            "s.relative_path, s.media_type, s.content_hash AS source_hash "
            "FROM knowledge_chunks c "
            "JOIN knowledge_sources s "
            "  ON s.source_id = c.source_id AND s.active_version = c.version_id "
            "JOIN knowledge_versions v ON v.source_id = c.source_id "
            "AND v.version_id = c.version_id "
            "WHERE c.embedding IS NOT NULL AND v.embedding_provider = $3 "
            "AND v.embedding_model = $4 AND v.embedding_dim = $5 "
            f"AND v.embedding_version = $6{clause}) "
            "SELECT source_id, version_id, ordinal, text, locator, heading_path, "
            "content_hash, root_key, relative_path, media_type, source_hash, "
            "1 - (embedding <=> $1::vector) AS score FROM matching "
            "ORDER BY embedding <=> $1::vector, source_id, version_id, ordinal "
            "LIMIT $2"
        )
        return await self._rows(
            statement,
            [
                _vector_literal(vector),
                limit,
                identity.provider,
                identity.model,
                identity.dim,
                identity.version,
                *parameters,
            ],
        )

    async def fetch_chunks(
        self, keys: Sequence[tuple[str, str, int]]
    ) -> list[dict[str, Any]]:
        if not keys:
            return []
        source_ids = [key[0] for key in keys]
        version_ids = [key[1] for key in keys]
        ordinals = [key[2] for key in keys]
        statement = (
            _CHUNK_SELECT + " "
            "JOIN unnest($1::text[], $2::text[], $3::int[]) "
            "  AS k(source_id, version_id, ordinal) "
            "  ON k.source_id = c.source_id AND k.version_id = c.version_id "
            "  AND k.ordinal = c.ordinal"
        )
        return await self._rows(statement, [source_ids, version_ids, ordinals])

    # -- helpers ------------------------------------------------------------

    async def _rows(
        self, statement: str, parameters: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        result = await self._data.execute(statement, list(parameters))
        rows = result.get("rows")
        if not isinstance(rows, list):
            return []
        return [dict(cast(Mapping[str, Any], row)) for row in rows]


def parse_locator(value: Any) -> Locator:
    data: Mapping[str, Any]
    if isinstance(value, Mapping):
        data = value
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = {}
        data = decoded if isinstance(decoded, Mapping) else {}
    else:
        data = {}
    try:
        kind = str(data.get("kind", "line_range"))
        start = int(cast(int, data.get("start", 0)))
        end = int(cast(int, data.get("end", 0)))
    except (TypeError, ValueError):
        kind, start, end = "line_range", 0, -1
    page = data.get("page")
    page_number = int(page) if isinstance(page, int) and not isinstance(page, bool) else None
    label = str(data.get("label", ""))
    return Locator(kind=kind, start=start, end=end, page=page_number, label=label)


def chunk_record(row: Mapping[str, Any]) -> ChunkRecord:
    heading_path = str(row.get("heading_path", ""))
    return ChunkRecord(
        source_id=str(row.get("source_id", "")),
        version_id=str(row.get("version_id", "")),
        ordinal=int(cast(int, row.get("ordinal", 0))),
        text=str(row.get("text", "")),
        locator=parse_locator(row.get("locator")),
        heading_path=tuple(part for part in heading_path.split("\n") if part),
        content_hash=str(row.get("content_hash", "")),
        root_key=str(row.get("root_key", "")),
        relative_path=str(row.get("relative_path", "")),
        media_type=str(row.get("media_type", "")),
        source_hash=str(row.get("source_hash", "")),
    )


def _filter_clause(
    filters: Mapping[str, Sequence[str]], *, start: int
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    index = start
    column_map = {
        "root_keys": "s.root_key",
        "media_types": "s.media_type",
        "source_ids": "s.source_id",
    }
    for name, column in column_map.items():
        values = filters.get(name)
        if not values:
            continue
        clauses.append(f" AND {column} = ANY(${index}::text[])")
        parameters.append(list(values))
        index += 1
    return "".join(clauses), parameters


def _vector_literal(vector: Sequence[float] | None) -> str | None:
    if vector is None:
        return None
    return "[" + ",".join(f"{float(value):.8f}" for value in vector) + "]"


def _source_record(row: Mapping[str, Any]) -> SourceRecord:
    active = row.get("active_version")
    return SourceRecord(
        source_id=str(row.get("source_id", "")),
        root_key=str(row.get("root_key", "")),
        relative_path=str(row.get("relative_path", "")),
        media_type=str(row.get("media_type", "")),
        size_bytes=int(cast(int, row.get("size_bytes", 0))),
        mtime_ns=int(cast(int, row.get("mtime_ns", 0))),
        content_hash=str(row.get("content_hash", "")),
        active_version=str(active) if active else None,
        generation=int(cast(int, row.get("generation", 0))),
    )


__all__ = ["KnowledgeStore", "chunk_record", "parse_locator"]
