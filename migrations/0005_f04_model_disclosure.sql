-- F04: persistent model disclosure consents. Additive only: 0001-0004 are
-- immutable history and are never edited. All statements are idempotent so the
-- migration runner may retry a partially-failed database safely.
--
-- Only metadata and digests are stored. Raw field values never reach these
-- tables; `field_digest` binds provider, purpose and the exact classified field
-- set (name/value hash/classification/source) and `recipient_fingerprint`
-- binds the concrete receiver (provider id, adapter, normalized endpoint and
-- model) so repointing a provider id cannot reuse an old consent.

CREATE TABLE IF NOT EXISTS model_disclosure_consents (
    id text PRIMARY KEY,
    owner_id text NOT NULL,
    provider_id text NOT NULL,
    purpose text NOT NULL,
    field_digest char(64) NOT NULL,
    recipient_fingerprint char(64) NOT NULL,
    field_count integer NOT NULL CHECK (field_count > 0),
    policy_version text NOT NULL,
    state text NOT NULL CHECK (state IN ('ACTIVE', 'REVOKED', 'EXPIRED')),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    version bigint NOT NULL DEFAULT 0,
    CHECK (expires_at > created_at),
    CHECK (state <> 'REVOKED' OR revoked_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS model_disclosure_consents_lookup_idx
    ON model_disclosure_consents
       (owner_id, provider_id, purpose, field_digest, recipient_fingerprint,
        policy_version, state, expires_at);

-- Idempotent command journal: one row per (command scope, idempotency key)
-- bound to the exact command fingerprint. A replay returns the first result; a
-- reused key with different content is a conflict.
CREATE TABLE IF NOT EXISTS model_disclosure_commands (
    scope text NOT NULL,
    idempotency_key text NOT NULL,
    command_fingerprint char(64) NOT NULL,
    consent_id text NOT NULL REFERENCES model_disclosure_consents(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, idempotency_key)
);

CREATE INDEX IF NOT EXISTS model_disclosure_commands_consent_idx
    ON model_disclosure_commands (consent_id);
