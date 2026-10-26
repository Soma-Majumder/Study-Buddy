-- Bind learner-visible quizzes to the exact privately approved candidate.
CREATE TABLE quiz_quality_publications (
  operation_id TEXT PRIMARY KEY REFERENCES quiz_quality_operations(operation_id),
  candidate_version INTEGER NOT NULL CHECK (candidate_version IN (0, 1)),
  candidate_hash TEXT NOT NULL,
  review_call_id TEXT NOT NULL UNIQUE REFERENCES quiz_quality_reports(review_call_id),
  quiz_id TEXT NOT NULL UNIQUE REFERENCES quizzes(quiz_id),
  published_at REAL NOT NULL,
  FOREIGN KEY (operation_id, candidate_version) REFERENCES quiz_quality_candidates(operation_id, candidate_version)
);
