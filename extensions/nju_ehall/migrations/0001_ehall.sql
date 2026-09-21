-- Extension-owned bookkeeping for supervised ehall transactions.
-- No credentials, cookies, verification codes or raw page HTML are stored.
CREATE TABLE IF NOT EXISTS ehall_transactions (
    transaction_id text PRIMARY KEY,
    adapter_id text NOT NULL,
    adapter_version text NOT NULL,
    app_id text NOT NULL,
    page_fingerprint char(64) NOT NULL,
    preview_hash char(64),
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ehall_actions (
    action_id text PRIMARY KEY,
    transaction_id text NOT NULL,
    kind text NOT NULL,
    payload_digest char(64) NOT NULL,
    status text NOT NULL,
    detail jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
