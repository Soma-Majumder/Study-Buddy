-- One Assessment feedback operation per already graded attempt.
-- A running operation is not automatically restarted after a crash: recovery
-- must reconcile it before any further model calls or routing.
CREATE TABLE feedback_operations (
  attempt_id TEXT PRIMARY KEY REFERENCES attempts (attempt_id),
  handoff_id TEXT NOT NULL UNIQUE REFERENCES handoff_repairs (handoff_id),
  status TEXT NOT NULL CHECK (status IN ('running', 'ready', 'failed'))
);
