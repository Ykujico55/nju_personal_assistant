-- F02: request-scoped idempotency for extension operations.
--
-- After a restart the process-local install plan no longer exists, so replay
-- cannot rely on (extension_id, operation, idempotency_key): the extension id
-- for an install is only known after parsing a plan.  The request scope is
-- derived purely from the HTTP route and request body, so a retried command can
-- be replayed from durable state alone.  Existing rows keep NULL scope.

ALTER TABLE extension_operations
    ADD COLUMN IF NOT EXISTS request_scope text;

CREATE UNIQUE INDEX IF NOT EXISTS extension_operations_request_scope_key_idx
    ON extension_operations (request_scope, idempotency_key)
    WHERE idempotency_key IS NOT NULL AND request_scope IS NOT NULL;
