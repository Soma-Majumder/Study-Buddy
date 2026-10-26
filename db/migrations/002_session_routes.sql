-- Persist the controller's decision for an already graded/feedback-ready attempt.
CREATE TABLE session_routes (
  attempt_id TEXT PRIMARY KEY REFERENCES attempts (attempt_id),
  session_id TEXT NOT NULL REFERENCES study_sessions (session_id),
  route TEXT NOT NULL CHECK (route IN ('reteach', 'summary')),
  context TEXT NOT NULL, -- JSON; controller-built context, never private answer keys
  status TEXT NOT NULL CHECK (status IN ('reserved', 'summary_pending', 'dispatched', 'failed'))
);
CREATE UNIQUE INDEX one_learning_retry_route ON session_routes (session_id) WHERE route = 'reteach';
