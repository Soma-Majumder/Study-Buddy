-- Final learner-scoped summary, saved before the session becomes completed.
CREATE TABLE session_summaries (
  session_id TEXT PRIMARY KEY REFERENCES study_sessions (session_id),
  final_attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts (attempt_id),
  summary TEXT NOT NULL, -- JSON built from saved controller-verified evidence
  saved_at TEXT NOT NULL
);
