-- nju.smail extension schema (runs inside the extension's own ext_* namespace).
--
-- Business data lives here, never in core tables.  Passwords never appear:
-- only non-secret account metadata and the opaque credential handle id.

CREATE TABLE IF NOT EXISTS mail_folders (
    account_id text NOT NULL,
    folder_name text NOT NULL,
    delimiter text,
    attributes jsonb NOT NULL DEFAULT '[]'::jsonb,
    selectable boolean NOT NULL DEFAULT true,
    uidvalidity bigint,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, folder_name)
);

CREATE TABLE IF NOT EXISTS mail_sync_state (
    account_id text NOT NULL,
    folder_name text NOT NULL,
    uidvalidity bigint NOT NULL DEFAULT 0,
    last_uid bigint NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'IDLE',
    error_code text,
    failure_count integer NOT NULL DEFAULT 0,
    backoff_until timestamptz,
    last_sync_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, folder_name)
);

CREATE TABLE IF NOT EXISTS mail_messages (
    message_pk text PRIMARY KEY,
    account_id text NOT NULL,
    dedupe_key text NOT NULL,
    message_id text,
    content_hash char(64) NOT NULL,
    subject text NOT NULL DEFAULT '',
    normalized_subject text NOT NULL DEFAULT '',
    snippet text NOT NULL DEFAULT '',
    from_address text,
    from_name text NOT NULL DEFAULT '',
    sent_at timestamptz,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    has_attachments boolean NOT NULL DEFAULT false,
    truncated boolean NOT NULL DEFAULT false,
    thread_id text,
    UNIQUE (account_id, dedupe_key)
);

CREATE TABLE IF NOT EXISTS mail_message_locations (
    account_id text NOT NULL,
    folder_name text NOT NULL,
    uidvalidity bigint NOT NULL,
    uid bigint NOT NULL,
    message_pk text NOT NULL,
    flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    size_bytes integer NOT NULL DEFAULT 0,
    seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, folder_name, uidvalidity, uid)
);

CREATE TABLE IF NOT EXISTS mail_message_people (
    message_pk text NOT NULL,
    role text NOT NULL,
    address text NOT NULL,
    display_name text NOT NULL DEFAULT '',
    PRIMARY KEY (message_pk, role, address)
);

CREATE TABLE IF NOT EXISTS mail_attachments (
    attachment_id text PRIMARY KEY,
    message_pk text NOT NULL,
    filename text NOT NULL,
    media_type text NOT NULL DEFAULT 'application/octet-stream',
    size_bytes integer NOT NULL DEFAULT 0,
    sha256 char(64) NOT NULL,
    part_index integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mail_contacts (
    account_id text NOT NULL,
    address text NOT NULL,
    display_name text NOT NULL DEFAULT '',
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    message_count integer NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, address)
);

CREATE TABLE IF NOT EXISTS mail_threads (
    thread_id text PRIMARY KEY,
    account_id text NOT NULL,
    normalized_subject text NOT NULL DEFAULT '',
    last_message_at timestamptz,
    message_count integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mail_drafts (
    draft_id text PRIMARY KEY,
    account_id text NOT NULL,
    current_version integer NOT NULL DEFAULT 0,
    thread_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mail_draft_versions (
    draft_id text NOT NULL,
    version integer NOT NULL,
    account_id text NOT NULL,
    account_fingerprint char(64) NOT NULL,
    from_address text NOT NULL,
    to_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    cc_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    bcc_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    subject text NOT NULL DEFAULT '',
    body_text text NOT NULL DEFAULT '',
    body_html text,
    attachment_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
    canonical_digest char(64) NOT NULL,
    mime_sha256 char(64) NOT NULL,
    mime_artifact_id text NOT NULL,
    local_action_id text NOT NULL,
    revision_request_id text NOT NULL,
    message_id text NOT NULL,
    in_reply_to text,
    refs_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    thread_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (draft_id, version)
);

CREATE TABLE IF NOT EXISTS mail_send_actions (
    local_action_id text PRIMARY KEY,
    account_id text NOT NULL,
    draft_id text NOT NULL,
    draft_version integer NOT NULL,
    message_id text NOT NULL,
    envelope_digest char(64) NOT NULL,
    state text NOT NULL DEFAULT 'PREPARED',
    receipt jsonb,
    recipient_results jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mail_events (
    sequence bigserial PRIMARY KEY,
    event_type text NOT NULL,
    dedupe_key text NOT NULL UNIQUE,
    account_id text NOT NULL,
    folder_name text NOT NULL,
    message_pk text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mail_messages_identity_idx
    ON mail_messages (account_id, message_id, content_hash);

CREATE INDEX IF NOT EXISTS mail_messages_thread_idx
    ON mail_messages (account_id, thread_id);

CREATE INDEX IF NOT EXISTS mail_message_locations_message_idx
    ON mail_message_locations (message_pk);

CREATE INDEX IF NOT EXISTS mail_draft_versions_action_idx
    ON mail_draft_versions (local_action_id);

CREATE UNIQUE INDEX IF NOT EXISTS mail_draft_versions_request_idx
    ON mail_draft_versions (draft_id, revision_request_id);
