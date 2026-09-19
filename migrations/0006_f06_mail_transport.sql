-- F06: generic host mail-transport delivery ledger.
--
-- Records the durable dispatch state machine for SMTP attempts made behind the
-- Tool Gateway:
--
--   PREPARED -> EXECUTING -> SUCCEEDED | PARTIAL | FAILED | UNKNOWN
--
-- The PREPARED/EXECUTING states are written before any SMTP connection is
-- opened, terminal states never move back, and only a persisted UNKNOWN may be
-- lifted by read-only reconciliation.  It contains no message body, no
-- credentials and no business-extension identifiers.

CREATE TABLE IF NOT EXISTS mail_delivery_actions (
    local_action_id text PRIMARY KEY,
    account_id text NOT NULL,
    message_id text NOT NULL,
    status text NOT NULL,
    envelope_digest char(64) NOT NULL,
    mime_sha256 char(64) NOT NULL,
    recipient_results jsonb NOT NULL DEFAULT '[]'::jsonb,
    server_code text,
    diagnostic_code text,
    owner_id text,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mail_delivery_actions_status_check
        CHECK (
            status IN (
                'PREPARED', 'EXECUTING', 'SUCCEEDED',
                'PARTIAL', 'FAILED', 'UNKNOWN'
            )
        )
);

CREATE INDEX IF NOT EXISTS mail_delivery_actions_message_idx
    ON mail_delivery_actions (account_id, message_id);

CREATE INDEX IF NOT EXISTS mail_delivery_actions_unresolved_idx
    ON mail_delivery_actions (status)
    WHERE status IN ('PREPARED', 'EXECUTING', 'UNKNOWN');

CREATE INDEX IF NOT EXISTS mail_delivery_actions_executing_lease_idx
    ON mail_delivery_actions (lease_expires_at)
    WHERE status = 'EXECUTING';
