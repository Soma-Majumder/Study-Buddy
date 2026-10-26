-- Controller-owned quality state only; not wired into quiz publication yet.
CREATE TABLE quiz_quality_operations (
  operation_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES study_sessions(session_id),
  learner_id TEXT NOT NULL REFERENCES learners(learner_id),
  lesson_id TEXT NOT NULL,
  lesson_version INTEGER NOT NULL,
  rubric_version TEXT NOT NULL DEFAULT '1.0' CHECK (rubric_version = '1.0'),
  hash_version TEXT NOT NULL DEFAULT 'private_quiz_v1' CHECK (hash_version = 'private_quiz_v1'),
  handoff_id TEXT NOT NULL UNIQUE REFERENCES handoff_repairs(handoff_id),
  explanation_id TEXT NOT NULL REFERENCES explanations(explanation_id),
  round TEXT NOT NULL CHECK (round IN ('first', 'retry')),
  stage TEXT NOT NULL CHECK (stage IN ('generating', 'generation_invalid', 'reviewing',
    'needs_revision', 'revising', 'revision_invalid', 'reviewing_revision', 'approved', 'failed')),
  candidate_version INTEGER NOT NULL DEFAULT 0 CHECK (candidate_version IN (0, 1)),
  candidate_hash TEXT,
  revision_count INTEGER NOT NULL DEFAULT 0 CHECK (revision_count BETWEEN 0 AND 1),
  review_count INTEGER NOT NULL DEFAULT 0 CHECK (review_count BETWEEN 0 AND 2),
  started_at REAL NOT NULL,
  deadline_at REAL NOT NULL CHECK (deadline_at > started_at),
  failure_code TEXT,
  UNIQUE (session_id, round),
  FOREIGN KEY (lesson_id, lesson_version) REFERENCES lessons(lesson_id, version)
);

CREATE TABLE quiz_quality_calls (
  call_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL REFERENCES quiz_quality_operations(operation_id),
  kind TEXT NOT NULL CHECK (kind IN ('generation', 'technical_repair', 'review', 'revision', 'review_revision')),
  status TEXT NOT NULL CHECK (status IN ('reserved', 'completed', 'failed')),
  candidate_version INTEGER NOT NULL CHECK (candidate_version IN (0, 1)),
  candidate_hash TEXT,
  reserved_at REAL NOT NULL,
  finished_at REAL,
  outcome TEXT,
  result_fingerprint TEXT,
  UNIQUE (operation_id, kind)
);
