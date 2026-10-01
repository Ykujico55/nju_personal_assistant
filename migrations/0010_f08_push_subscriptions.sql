-- F08.5: endpoint and browser keys are AES-GCM sealed by the host before insert.
CREATE TABLE push_subscriptions (
    owner_id text NOT NULL,
    id char(64) NOT NULL,
    sealed bytea NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (owner_id, id),
    CONSTRAINT push_subscription_sealed_minimum CHECK (octet_length(sealed) >= 29)
);
