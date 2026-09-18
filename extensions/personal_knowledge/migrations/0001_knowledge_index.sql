-- Personal knowledge index schema (extension-owned).
--
-- This file is executed by the host's generic extension data capability inside
-- the extension's own `ext_*` namespace.  Core tables are never touched.
-- `to_tsvector('simple', ...)` is immutable and safe in a generated column.
-- Embeddings are stored in an unconstrained `vector` column so a model with a
-- different dimension triggers a rebuild (identity is persisted per version)
-- instead of silently mixing vector spaces.

CREATE TABLE IF NOT EXISTS knowledge_sources (
    source_id text PRIMARY KEY,
    root_key text NOT NULL,
    relative_path text NOT NULL,
    media_type text NOT NULL,
    size_bytes bigint NOT NULL,
    mtime_ns bigint NOT NULL,
    content_hash char(64) NOT NULL,
    active_version char(64),
    generation bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (root_key, relative_path)
);

CREATE TABLE IF NOT EXISTS knowledge_versions (
    source_id text NOT NULL,
    version_id char(64) NOT NULL,
    content_hash char(64) NOT NULL,
    state text NOT NULL CHECK (state IN ('BUILDING', 'READY', 'FAILED')),
    extractor_name text NOT NULL,
    extractor_version text NOT NULL,
    embedding_provider text NOT NULL,
    embedding_model text NOT NULL,
    embedding_dim integer NOT NULL DEFAULT 0,
    embedding_version text NOT NULL,
    chunk_count integer NOT NULL DEFAULT 0,
    diagnostics jsonb NOT NULL DEFAULT '[]'::jsonb,
    built_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (source_id, version_id)
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    source_id text NOT NULL,
    version_id char(64) NOT NULL,
    ordinal integer NOT NULL,
    text text NOT NULL,
    locator jsonb NOT NULL,
    heading_path text NOT NULL DEFAULT '',
    page integer,
    line_start integer,
    line_end integer,
    char_start integer,
    char_end integer,
    content_hash char(64) NOT NULL,
    embedding vector,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    PRIMARY KEY (source_id, version_id, ordinal),
    FOREIGN KEY (source_id, version_id)
        REFERENCES knowledge_versions (source_id, version_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS knowledge_chunks_source_idx
    ON knowledge_chunks (source_id, version_id);
CREATE INDEX IF NOT EXISTS knowledge_chunks_fts_idx
    ON knowledge_chunks USING gin (search_vector);

-- The embedding column is intentionally unconstrained so a model with a
-- different dimension triggers a rebuild instead of mixing vector spaces.
-- pgvector cannot index an unconstrained column, so vector search is exact
-- (``<=>`` over the active version); a dimension-specific HNSW index can be
-- added later once the deployment fixes a single local embedding model.
CREATE INDEX IF NOT EXISTS knowledge_chunks_active_idx
    ON knowledge_chunks (source_id, version_id);

CREATE TABLE IF NOT EXISTS knowledge_tombstones (
    source_id text PRIMARY KEY,
    root_key text NOT NULL,
    relative_path text NOT NULL,
    last_content_hash char(64) NOT NULL,
    generation bigint NOT NULL DEFAULT 0,
    deleted_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_events (
    sequence bigserial PRIMARY KEY,
    event_type text NOT NULL,
    source_id text NOT NULL,
    dedupe_key text NOT NULL UNIQUE,
    root_key text NOT NULL,
    relative_path text NOT NULL,
    content_hash char(64),
    previous_hash char(64),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS knowledge_meta (
    key text PRIMARY KEY,
    value jsonb NOT NULL
);
