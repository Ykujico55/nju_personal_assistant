-- F02: idempotent extension operations.
--
-- The F01 extension_operations table only stored one row per operation.  F02
-- needs to replay a command by its Idempotency-Key and to reject a reused key
-- with a different command fingerprint, so the key and the canonical command
-- fingerprint become durable columns.  The partial unique index is the
-- cross-process guard: two Admin starts cannot both create the same command.
-- Existing rows keep NULL keys and are unaffected.

ALTER TABLE extension_operations
    ADD COLUMN IF NOT EXISTS idempotency_key text;
ALTER TABLE extension_operations
    ADD COLUMN IF NOT EXISTS command_fingerprint text;

CREATE UNIQUE INDEX IF NOT EXISTS extension_operations_idempotency_key_idx
    ON extension_operations (extension_id, operation, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
