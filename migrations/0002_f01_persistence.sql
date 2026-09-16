-- F01 persistence completion. This migration is additive only: 0001_core.sql is
-- immutable history and is never edited. All statements are idempotent so the
-- migration runner may retry a partially-failed database safely.

-- Task command idempotency: one durable record per (command scope, key).
CREATE TABLE IF NOT EXISTS task_command_idempotency (
    scope text NOT NULL,
    idempotency_key text NOT NULL,
    request_sha256 char(64) NOT NULL,
    task_id text NOT NULL,
    message_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, idempotency_key)
);
CREATE INDEX IF NOT EXISTS task_command_task_idx
    ON task_command_idempotency (task_id);

CREATE TABLE IF NOT EXISTS task_messages (
    id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    actor text NOT NULL,
    content text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS task_messages_task_idx
    ON task_messages (task_id, created_at, id);

-- Durable task event log; sequence is the cross-process single source of truth.
CREATE TABLE IF NOT EXISTS task_events (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type text NOT NULL,
    task_id text NOT NULL,
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS task_events_task_idx ON task_events (task_id, sequence);

-- TaskRun full domain fields (0001 agent_runs was a partial skeleton).
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS objective text;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS segment_number integer NOT NULL DEFAULT 1;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS active_started_at timestamptz;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS same_call_without_progress integer NOT NULL DEFAULT 0;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS last_call_fingerprint text;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS last_observation_fingerprint text;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS waiting_reference text;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS pause_reason text;

-- Backfill existing 0001 rows from the authoritative task/start facts, then
-- enforce the domain contract. A populated 0001 database must stay readable.
UPDATE agent_runs AS r
   SET objective = t.objective
  FROM tasks AS t
 WHERE r.task_id = t.id AND r.objective IS NULL;
UPDATE agent_runs SET active_started_at = started_at WHERE active_started_at IS NULL;
ALTER TABLE agent_runs ALTER COLUMN objective SET NOT NULL;
ALTER TABLE agent_runs ALTER COLUMN active_started_at SET NOT NULL;

-- Checkpoint ordering plus the exact run version the snapshot belongs to.
CREATE SEQUENCE IF NOT EXISTS run_checkpoint_sequence;
-- Advance the new sequence past any sequence values already present in a
-- populated 0001 database; otherwise the first append for an existing run
-- would restart at 1 and collide with the (run_id, sequence) primary key.
SELECT setval(
    'run_checkpoint_sequence',
    GREATEST(COALESCE((SELECT max(sequence) FROM run_checkpoints), 1), 1),
    (SELECT max(sequence) FROM run_checkpoints) IS NOT NULL
);
ALTER TABLE run_checkpoints ADD COLUMN IF NOT EXISTS run_version bigint NOT NULL DEFAULT 0;

-- Append-only observations for resumed runs.
CREATE TABLE IF NOT EXISTS run_observations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    value jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_observations_run_idx ON run_observations (run_id, id);

-- ApprovalRecord completion and compare-and-swap version.
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 0;
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS approved_at timestamptz;
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS approved_by text;
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS completed_at timestamptz;
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS result_reference text;
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS failure_reason text;

-- Job metadata (IDs and references only; never secrets or full payloads).
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Side-effect intents / outbox. (tool_id, idempotency_key) is the effectively-once
-- key; the action fingerprint binds the approved target/payload/extension snapshot.
CREATE TABLE IF NOT EXISTS side_effect_intents (
    id text PRIMARY KEY,
    task_id text NOT NULL,
    tool_id text NOT NULL,
    idempotency_key text NOT NULL,
    approval_id text NOT NULL,
    canonical_payload_sha256 char(64) NOT NULL,
    action_fingerprint char(64) NOT NULL,
    state text NOT NULL,
    receipt jsonb NOT NULL DEFAULT '{}'::jsonb,
    diagnostic_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tool_id, idempotency_key),
    CHECK (state IN ('PREPARED', 'EXECUTING', 'SUCCEEDED', 'FAILED', 'UNKNOWN'))
);

-- Extension lifecycle state: full manifest/version association plus tombstone.
ALTER TABLE extensions ADD COLUMN IF NOT EXISTS manifest jsonb;
ALTER TABLE extensions ADD COLUMN IF NOT EXISTS manifest_version text;
ALTER TABLE extensions ADD COLUMN IF NOT EXISTS artifact_hash text;
ALTER TABLE extensions ADD COLUMN IF NOT EXISTS install_path text;
ALTER TABLE extensions ADD COLUMN IF NOT EXISTS tombstone boolean NOT NULL DEFAULT false;

-- Backfill existing 0001 extensions from their recorded version manifest. The
-- package version column is not the manifest format version: the latter must be
-- read from the manifest JSON. Install path is preserved from the version row.
UPDATE extensions AS e
   SET manifest = v.manifest,
       manifest_version = COALESCE(v.manifest->>'manifest_version', '1'),
       artifact_hash = COALESCE(e.artifact_hash, 'sha256:' || v.source_sha256),
       install_path = COALESCE(e.install_path, v.install_path)
  FROM extension_versions AS v
 WHERE v.extension_id = e.id
   AND v.version = e.active_version
   AND e.manifest IS NULL;
