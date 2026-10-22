-- Study Buddy shared records (S2). Database engine is still an open decision,
-- so this is plain SQL that runs on SQLite and is easy to port to Postgres.
-- Only controller code writes to these tables (PRD: "Controller code only").
-- IDs are text; formats are defined in docs/ids-and-records.md.

PRAGMA foreign_keys = ON;

CREATE TABLE lessons (
  lesson_id    TEXT NOT NULL,
  version      INTEGER NOT NULL CHECK (version >= 1),
  topic        TEXT NOT NULL,
  source_text  TEXT NOT NULL,
  source_url   TEXT NOT NULL,
  license      TEXT NOT NULL,
  attribution  TEXT NOT NULL,
  approved     INTEGER NOT NULL DEFAULT 0 CHECK (approved IN (0, 1)),
  PRIMARY KEY (lesson_id, version)
);

CREATE TABLE concepts (
  concept_id     TEXT PRIMARY KEY,
  lesson_id      TEXT NOT NULL,
  lesson_version INTEGER NOT NULL,
  name           TEXT NOT NULL,
  summary        TEXT NOT NULL,
  FOREIGN KEY (lesson_id, lesson_version) REFERENCES lessons (lesson_id, version)
);

CREATE TABLE learners (
  learner_id TEXT PRIMARY KEY
);

CREATE TABLE study_sessions (
  session_id            TEXT PRIMARY KEY,
  learner_id            TEXT NOT NULL REFERENCES learners (learner_id),
  lesson_id             TEXT NOT NULL,
  lesson_version        INTEGER NOT NULL,
  status                TEXT NOT NULL CHECK (status IN (
                          'started', 'teaching', 'awaiting_answers', 'grading',
                          'reteaching', 'pending_save', 'completed', 'failed')),
  failure_reason        TEXT,
  learning_retry_count  INTEGER NOT NULL DEFAULT 0 CHECK (learning_retry_count BETWEEN 0 AND 1),
  created_at            TEXT NOT NULL,
  FOREIGN KEY (lesson_id, lesson_version) REFERENCES lessons (lesson_id, version)
);

-- Technical repair budget, separate from learning retries. One row per handoff.
CREATE TABLE handoff_repairs (
  handoff_id   TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL REFERENCES study_sessions (session_id),
  direction    TEXT NOT NULL CHECK (direction IN ('tutor_to_assessment', 'assessment_to_tutor')),
  repair_count INTEGER NOT NULL DEFAULT 0 CHECK (repair_count BETWEEN 0 AND 1)
);

CREATE TABLE explanations (
  explanation_id TEXT PRIMARY KEY,
  session_id     TEXT NOT NULL REFERENCES study_sessions (session_id),
  round          TEXT NOT NULL CHECK (round IN ('first', 'retry')),
  text           TEXT NOT NULL
);

CREATE TABLE explanation_concepts (
  explanation_id TEXT NOT NULL REFERENCES explanations (explanation_id),
  concept_id     TEXT NOT NULL REFERENCES concepts (concept_id),
  PRIMARY KEY (explanation_id, concept_id)
);

CREATE TABLE quizzes (
  quiz_id        TEXT PRIMARY KEY,
  session_id     TEXT NOT NULL REFERENCES study_sessions (session_id),
  explanation_id TEXT NOT NULL REFERENCES explanations (explanation_id),
  round          TEXT NOT NULL CHECK (round IN ('first', 'retry'))
);

-- Student-facing question content. Never contains the answer.
CREATE TABLE questions (
  question_id TEXT PRIMARY KEY,
  quiz_id     TEXT NOT NULL REFERENCES quizzes (quiz_id),
  position    INTEGER NOT NULL CHECK (position BETWEEN 1 AND 10),
  concept_id  TEXT NOT NULL REFERENCES concepts (concept_id),
  prompt      TEXT NOT NULL,
  option_a    TEXT NOT NULL,
  option_b    TEXT NOT NULL,
  option_c    TEXT NOT NULL,
  option_d    TEXT NOT NULL,
  UNIQUE (quiz_id, position)
);

-- Private answer keys. Backend only; never sent to the client before submission.
CREATE TABLE answer_keys (
  question_id    TEXT PRIMARY KEY REFERENCES questions (question_id),
  correct_option TEXT NOT NULL CHECK (correct_option IN ('a', 'b', 'c', 'd'))
);

CREATE TABLE attempts (
  attempt_id   TEXT PRIMARY KEY,
  quiz_id      TEXT NOT NULL REFERENCES quizzes (quiz_id),
  learner_id   TEXT NOT NULL REFERENCES learners (learner_id),
  submitted_at TEXT NOT NULL
);

CREATE TABLE responses (
  attempt_id      TEXT NOT NULL REFERENCES attempts (attempt_id),
  question_id     TEXT NOT NULL REFERENCES questions (question_id),
  selected_option TEXT NOT NULL CHECK (selected_option IN ('a', 'b', 'c', 'd')),
  is_correct      INTEGER NOT NULL CHECK (is_correct IN (0, 1)),  -- set by controller grading
  PRIMARY KEY (attempt_id, question_id)
);

-- Graded outcome of one attempt (one result per attempt).
CREATE TABLE results (
  result_id  TEXT PRIMARY KEY,
  attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts (attempt_id),
  score      INTEGER NOT NULL CHECK (score BETWEEN 0 AND 10),
  feedback   TEXT NOT NULL,
  passed     INTEGER NOT NULL CHECK (passed IN (0, 1)),
  recorded_at TEXT NOT NULL
);

CREATE TABLE weak_concepts (
  learner_id        TEXT NOT NULL REFERENCES learners (learner_id),
  concept_id        TEXT NOT NULL REFERENCES concepts (concept_id),
  source_attempt_id TEXT NOT NULL REFERENCES attempts (attempt_id),
  lesson_id         TEXT NOT NULL,
  missed_count      INTEGER NOT NULL CHECK (missed_count >= 1),
  recorded_at       TEXT NOT NULL,
  PRIMARY KEY (learner_id, concept_id, source_attempt_id)
);
