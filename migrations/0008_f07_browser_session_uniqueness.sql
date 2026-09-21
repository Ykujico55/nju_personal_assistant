-- F07 audit remediation: exactly one open supervised browser session per task
-- (with side-effect-safe convergence of pre-existing duplicates) and the
-- pre-click receipt baseline used by host-driven reconciliation.
--
-- A pending external action (EXECUTING/UNKNOWN) must never be cancelled by a
-- migration: it may already have reached the remote system.  Convergence is
-- therefore fail-closed:
--
--   1. if any task already has more than one pending session, the migration
--      refuses to run and leaves the database at 0007 for manual adjudication;
--   2. otherwise the single pending session per task (if any) is kept and only
--      sessions that are provably pre-side-effect (REQUESTED/WAITING_USER/
--      AUTHENTICATED/DISCOVERED/PREPARING/PREVIEW_READY/APPROVED/SAFETY_PAUSED)
--      may be converged away, keeping the earliest one;
--   3. tasks without a pending session keep their earliest open session.
--
-- The partial unique index then makes the invariant atomic.  UNKNOWN stays
-- inside the constraint so it keeps occupying the task slot until a read-only
-- reconciliation or a human decision resolves it.

ALTER TABLE browser_sessions
    ADD COLUMN IF NOT EXISTS receipt_baseline text NOT NULL DEFAULT '';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM browser_sessions
        WHERE state IN ('EXECUTING', 'UNKNOWN')
        GROUP BY task_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'multiple pending browser sessions require manual adjudication';
    END IF;
END $$;

WITH ranked AS (
    SELECT session_id,
           row_number() OVER (
               PARTITION BY task_id
               ORDER BY (state IN ('EXECUTING', 'UNKNOWN')) DESC,
                        created_at,
                        session_id
           ) AS rank
    FROM browser_sessions
    WHERE state NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED')
)
UPDATE browser_sessions AS session
SET state = 'CANCELLED',
    outcome = 'CONVERGED',
    diagnostic_code = 'DUPLICATE_CONVERGED',
    updated_at = now(),
    version = version + 1
FROM ranked
WHERE session.session_id = ranked.session_id
  AND ranked.rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS browser_sessions_task_open_unique_idx
    ON browser_sessions (task_id)
    WHERE state NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED');
