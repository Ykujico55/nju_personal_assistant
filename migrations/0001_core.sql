-- Core schema for PostgreSQL 17 + pgvector. Extension-owned tables must live in
-- separately named ext_* schemas and must never alter these tables.
BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tasks (
    id text PRIMARY KEY,
    owner_id text NOT NULL,
    objective text NOT NULL,
    status text NOT NULL,
    version bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN (
        'CREATED', 'QUEUED', 'RUNNING', 'WAITING_USER', 'WAITING_APPROVAL',
        'WAITING_RECONCILIATION', 'PAUSED_SAFETY', 'PAUSED_EXTENSION',
        'SUCCEEDED', 'FAILED', 'CANCELLED'
    ))
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES tasks(id),
    state text NOT NULL,
    round_count integer NOT NULL DEFAULT 0,
    no_progress_rounds integer NOT NULL DEFAULT 0,
    consecutive_errors integer NOT NULL DEFAULT 0,
    started_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    version bigint NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_checkpoints (
    run_id text NOT NULL REFERENCES agent_runs(id),
    sequence bigint NOT NULL,
    state text NOT NULL,
    snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, sequence)
);

CREATE TABLE IF NOT EXISTS approvals (
    id text PRIMARY KEY,
    owner_id text NOT NULL,
    task_id text NOT NULL REFERENCES tasks(id),
    action_id text NOT NULL,
    target jsonb NOT NULL,
    canonical_action jsonb NOT NULL,
    action_fingerprint char(64) NOT NULL,
    canonical_payload_sha256 char(64) NOT NULL,
    attachment_sha256 jsonb NOT NULL DEFAULT '[]'::jsonb,
    extension_id text NOT NULL,
    extension_version text NOT NULL,
    nonce text NOT NULL UNIQUE,
    status text NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN (
        'DRAFT', 'PREPARED', 'WAITING_APPROVAL', 'APPROVED', 'EXECUTING',
        'SUCCEEDED', 'FAILED', 'UNKNOWN', 'EXPIRED', 'CANCELLED'
    ))
);
CREATE INDEX IF NOT EXISTS approvals_pending_idx ON approvals (owner_id, status, expires_at);

CREATE TABLE IF NOT EXISTS jobs (
    id text PRIMARY KEY,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    idempotency_key text NOT NULL,
    state text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5,
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_until timestamptz,
    result jsonb,
    error_code text,
    version bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, idempotency_key),
    CHECK (state IN ('READY', 'LEASED', 'SUCCEEDED', 'FAILED', 'DEAD_LETTER', 'WAITING_RECONCILIATION'))
);
CREATE INDEX IF NOT EXISTS jobs_claim_idx ON jobs (state, available_at, created_at);
CREATE INDEX IF NOT EXISTS jobs_lease_idx ON jobs (lease_until) WHERE state = 'LEASED';

CREATE TABLE IF NOT EXISTS tool_invocations (
    id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES tasks(id),
    run_id text REFERENCES agent_runs(id),
    tool_id text NOT NULL,
    extension_id text NOT NULL,
    extension_version text NOT NULL,
    risk text NOT NULL,
    idempotency_key text NOT NULL,
    input_sha256 char(64) NOT NULL,
    status text NOT NULL,
    receipt jsonb,
    error_code text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE (tool_id, idempotency_key),
    CHECK (risk IN ('R0', 'R1', 'R2', 'R3')),
    CHECK (status IN ('PREPARED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN', 'BLOCKED'))
);

CREATE TABLE IF NOT EXISTS audit_events (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type text NOT NULL,
    actor text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_resource_idx
    ON audit_events (resource_type, resource_id, occurred_at);

CREATE TABLE IF NOT EXISTS extensions (
    id text PRIMARY KEY,
    active_version text,
    lifecycle_state text NOT NULL,
    retained_data boolean NOT NULL DEFAULT true,
    registry_generation bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS extension_versions (
    extension_id text NOT NULL REFERENCES extensions(id),
    version text NOT NULL,
    source_sha256 char(64) NOT NULL,
    manifest jsonb NOT NULL,
    install_path text,
    installed_at timestamptz,
    PRIMARY KEY (extension_id, version)
);

CREATE TABLE IF NOT EXISTS extension_operations (
    id text PRIMARY KEY,
    extension_id text NOT NULL,
    operation text NOT NULL,
    status text NOT NULL,
    diagnostic_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS artifacts (
    id text PRIMARY KEY,
    owner_id text NOT NULL,
    media_type text NOT NULL,
    sha256 char(64) NOT NULL,
    storage_key text NOT NULL UNIQUE,
    sensitivity text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE TABLE IF NOT EXISTS context_documents (
    id text PRIMARY KEY,
    owner_id text NOT NULL,
    source_uri text NOT NULL,
    source_sha256 char(64) NOT NULL,
    locator jsonb NOT NULL,
    content text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    embedding vector(1536),
    sensitivity text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);
CREATE INDEX IF NOT EXISTS context_fts_idx ON context_documents USING gin(search_vector);
CREATE INDEX IF NOT EXISTS context_source_idx ON context_documents (source_uri, source_sha256);

COMMIT;
