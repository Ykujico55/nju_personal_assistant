-- F08.3: one generic, versioned schema-form draft per task.  No approval or
-- external-action columns are present.  Command receipts make lost responses
-- safe to replay with the original Idempotency-Key.
CREATE TABLE IF NOT EXISTS task_form_drafts (
    task_id text PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    extension_id text NOT NULL,
    extension_version text NOT NULL,
    form_id text NOT NULL,
    json_schema jsonb NOT NULL,
    ui_schema jsonb NOT NULL,
    values_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    sources jsonb NOT NULL DEFAULT '{}'::jsonb,
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS task_form_draft_commands (
    idempotency_key text PRIMARY KEY,
    request_sha256 char(64) NOT NULL,
    receipt jsonb NOT NULL
);
