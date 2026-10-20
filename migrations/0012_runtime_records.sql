-- Generic host state; no business-extension identifiers or credentials.
CREATE TABLE runtime_records (
    owner_id text NOT NULL,
    bucket text NOT NULL,
    record_id text NOT NULL,
    value jsonb NOT NULL,
    version bigint NOT NULL CHECK (version >= 1),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, bucket, record_id)
);
CREATE TABLE runtime_commands (
    owner_id text NOT NULL,
    command_key text NOT NULL,
    fingerprint char(64) NOT NULL,
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, command_key)
);
