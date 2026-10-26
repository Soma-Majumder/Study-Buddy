-- Private backend staging. These are not learner-facing quiz tables.
CREATE TABLE quiz_quality_candidates (
  operation_id TEXT NOT NULL REFERENCES quiz_quality_operations(operation_id),
  candidate_version INTEGER NOT NULL CHECK (candidate_version IN (0, 1)),
  candidate_hash TEXT NOT NULL,
  generating_call_id TEXT NOT NULL UNIQUE REFERENCES quiz_quality_calls(call_id),
  question_data TEXT NOT NULL, -- quiz metadata/questions only; no answer_key
  explanation_text TEXT NOT NULL,
  concept_ids TEXT NOT NULL,
  stored_at REAL NOT NULL,
  PRIMARY KEY (operation_id, candidate_version)
);

CREATE TABLE quiz_quality_candidate_keys (
  operation_id TEXT NOT NULL,
  candidate_version INTEGER NOT NULL,
  position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 10),
  key_order INTEGER NOT NULL CHECK (key_order BETWEEN 0 AND 9),
  correct_option TEXT NOT NULL CHECK (correct_option IN ('a', 'b', 'c', 'd')),
  rationale TEXT NOT NULL,
  PRIMARY KEY (operation_id, candidate_version, position),
  UNIQUE (operation_id, candidate_version, key_order),
  FOREIGN KEY (operation_id, candidate_version) REFERENCES quiz_quality_candidates(operation_id, candidate_version)
);

CREATE TABLE quiz_quality_reports (
  review_call_id TEXT PRIMARY KEY REFERENCES quiz_quality_calls(call_id),
  operation_id TEXT NOT NULL,
  candidate_version INTEGER NOT NULL,
  candidate_hash TEXT NOT NULL,
  report_data TEXT NOT NULL, -- validated backend-only reviewer report
  report_fingerprint TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('pass', 'needs_revision', 'blocked')),
  stored_at REAL NOT NULL,
  UNIQUE (operation_id, candidate_version),
  FOREIGN KEY (operation_id, candidate_version) REFERENCES quiz_quality_candidates(operation_id, candidate_version)
);
