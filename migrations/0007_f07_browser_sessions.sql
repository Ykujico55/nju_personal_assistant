-- F07: supervised browser sessions and transaction adapter registry.
--
-- Host-owned, vendor-neutral bookkeeping for the Desktop Companion boundary:
--
--   REQUESTED -> WAITING_USER -> AUTHENTICATED -> DISCOVERED -> PREPARING
--   -> PREVIEW_READY [-> APPROVED -> EXECUTING -> SUCCEEDED|FAILED|UNKNOWN]
--
-- Standard F07 acceptance stops at PREVIEW_READY.  EXECUTING rows that lose
-- their owner are moved to UNKNOWN by startup recovery and may only leave
-- UNKNOWN through read-only flow reconciliation.  The tables contain no
-- cookies, storage state, passwords, verification codes, screenshots or
-- unbounded page HTML; ``preview``/``receipt`` only hold the bounded
-- structured documents defined by the F07 contract.
--
-- No business-extension identifier or page selector is embedded here: adapter
-- descriptors arrive as opaque JSON documents owned by an installed extension.

CREATE TABLE IF NOT EXISTS browser_sessions (
    session_id text PRIMARY KEY,
    task_id text NOT NULL,
    extension_id text NOT NULL,
    extension_version text NOT NULL,
    purpose text NOT NULL,
    state text NOT NULL,
    origin text NOT NULL DEFAULT '',
    url text NOT NULL DEFAULT '',
    adapter_id text NOT NULL DEFAULT '',
    adapter_version text NOT NULL DEFAULT '',
    app_id text NOT NULL DEFAULT '',
    transaction_id text NOT NULL DEFAULT '',
    page_fingerprint char(64),
    preview_hash char(64),
    preview_nonce text,
    preview jsonb,
    outcome text NOT NULL DEFAULT '',
    receipt jsonb,
    diagnostic_code text NOT NULL DEFAULT '',
    re_navigations integer NOT NULL DEFAULT 0,
    visited_paths text[] NOT NULL DEFAULT '{}',
    fill_count integer NOT NULL DEFAULT 0,
    submit_count integer NOT NULL DEFAULT 0,
    apps_count integer NOT NULL DEFAULT 0,
    owner_id text NOT NULL DEFAULT '',
    version integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    CONSTRAINT browser_sessions_state_check CHECK (state IN (
        'REQUESTED',
        'WAITING_USER',
        'AUTHENTICATED',
        'DISCOVERED',
        'PREPARING',
        'PREVIEW_READY',
        'APPROVED',
        'EXECUTING',
        'SUCCEEDED',
        'FAILED',
        'UNKNOWN',
        'CANCELLED',
        'SAFETY_PAUSED'
    ))
);

CREATE INDEX IF NOT EXISTS browser_sessions_task_created_idx
    ON browser_sessions (task_id, created_at);

CREATE INDEX IF NOT EXISTS browser_sessions_executing_owner_idx
    ON browser_sessions (owner_id, updated_at)
    WHERE state = 'EXECUTING';

CREATE TABLE IF NOT EXISTS browser_adapters (
    extension_id text NOT NULL,
    adapter_id text NOT NULL,
    adapter_version text NOT NULL,
    extension_version text NOT NULL,
    descriptor jsonb NOT NULL,
    version integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (extension_id, adapter_id, adapter_version)
);
