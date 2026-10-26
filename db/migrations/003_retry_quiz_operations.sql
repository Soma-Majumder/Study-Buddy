-- At most one focused retry-quiz generation operation per session/source attempt.
CREATE TABLE retry_quiz_operations (
  source_attempt_id TEXT PRIMARY KEY REFERENCES attempts (attempt_id),
  session_id TEXT NOT NULL UNIQUE REFERENCES study_sessions (session_id),
  handoff_id TEXT NOT NULL UNIQUE REFERENCES handoff_repairs (handoff_id),
  quiz_id TEXT UNIQUE REFERENCES quizzes (quiz_id),
  status TEXT NOT NULL CHECK (status IN ('running', 'ready', 'failed')),
  error TEXT
);
