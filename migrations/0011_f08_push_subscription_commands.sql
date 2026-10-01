-- Keep the original subscription receipt after revocation or a host policy change.
-- The request digest binds the complete endpoint and browser key material without
-- storing either capability in plaintext.
CREATE TABLE push_subscription_commands (
    owner_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_sha256 char(64) NOT NULL,
    subscription_id char(64) NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (owner_id, idempotency_key),
    CONSTRAINT push_subscription_command_digest CHECK
        (request_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT push_subscription_command_id CHECK
        (subscription_id ~ '^[0-9a-f]{64}$')
);
