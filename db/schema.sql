-- Study Buddy shared records (S2). Database engine is still an open decision,
-- so this is plain SQL that runs on SQLite and is easy to port to Postgres.
-- Only controller code writes to these tables (PRD: "Controller code only").
-- IDs are text; formats are defined in docs/ids-and-records.md.
-- Lesson JSON files in lessons/ are the source of truth; study_buddy/lessons.py loads them into
-- the lessons and concepts tables, so the two cannot drift.

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
  book         TEXT NOT NULL DEFAULT '',
  authors      TEXT NOT NULL DEFAULT '',
  adaptation_notice TEXT NOT NULL DEFAULT '',  -- shown with the lesson (CC BY-NC-SA: say what changed)
  approval     TEXT,                           -- JSON: who approved the teaching text, when, what changed
  PRIMARY KEY (lesson_id, version)
);

CREATE TABLE concepts (
  concept_id     TEXT PRIMARY KEY,
  lesson_id      TEXT NOT NULL,
  lesson_version INTEGER NOT NULL,
  name           TEXT NOT NULL,
  summary        TEXT NOT NULL,
  source_passage TEXT NOT NULL DEFAULT '',     -- approved excerpt used when reteaching this concept
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
  correct_option TEXT NOT NULL CHECK (correct_option IN ('a', 'b', 'c', 'd')),
  rationale      TEXT  -- why the option is right; private until the quiz is graded
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

-- One Assessment feedback operation per already graded attempt.
-- A running operation is not automatically restarted after a crash: recovery
-- must reconcile it before any further model calls or routing.
CREATE TABLE feedback_operations (
  attempt_id TEXT PRIMARY KEY REFERENCES attempts (attempt_id),
  handoff_id TEXT NOT NULL UNIQUE REFERENCES handoff_repairs (handoff_id),
  status TEXT NOT NULL CHECK (status IN ('running', 'ready', 'failed'))
);

-- Persist the controller's decision for an already graded/feedback-ready attempt.
CREATE TABLE session_routes (
  attempt_id TEXT PRIMARY KEY REFERENCES attempts (attempt_id),
  session_id TEXT NOT NULL REFERENCES study_sessions (session_id),
  route TEXT NOT NULL CHECK (route IN ('reteach', 'summary')),
  context TEXT NOT NULL, -- JSON; controller-built context, never private answer keys
  status TEXT NOT NULL CHECK (status IN ('reserved', 'summary_pending', 'dispatched', 'failed'))
);
CREATE UNIQUE INDEX one_learning_retry_route ON session_routes (session_id) WHERE route = 'reteach';

-- At most one focused retry-quiz generation operation per session/source attempt.
CREATE TABLE retry_quiz_operations (
  source_attempt_id TEXT PRIMARY KEY REFERENCES attempts (attempt_id),
  session_id TEXT NOT NULL UNIQUE REFERENCES study_sessions (session_id),
  handoff_id TEXT NOT NULL UNIQUE REFERENCES handoff_repairs (handoff_id),
  quiz_id TEXT UNIQUE REFERENCES quizzes (quiz_id),
  status TEXT NOT NULL CHECK (status IN ('running', 'ready', 'failed')),
  error TEXT
);

-- Final learner-scoped summary, saved before the session becomes completed.
CREATE TABLE session_summaries (
  session_id TEXT PRIMARY KEY REFERENCES study_sessions (session_id),
  final_attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts (attempt_id),
  summary TEXT NOT NULL, -- JSON built from saved controller-verified evidence
  saved_at TEXT NOT NULL
);

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
-- Persist the validated original Tutor handoff for crash-safe continuation.
CREATE TABLE quiz_quality_inputs (
  operation_id TEXT PRIMARY KEY REFERENCES quiz_quality_operations(operation_id),
  handoff_data TEXT NOT NULL
);

-- Durable Start-request reservations (migration 009).
CREATE TABLE session_start_requests (
  learner_id TEXT NOT NULL REFERENCES learners(learner_id),
  request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 8 AND 128),
  lesson_id TEXT NOT NULL,
  lesson_version INTEGER NOT NULL CHECK(lesson_version >= 1),
  session_id TEXT NOT NULL UNIQUE REFERENCES study_sessions(session_id),
  handoff_id TEXT NOT NULL UNIQUE REFERENCES handoff_repairs(handoff_id),
  explanation_id TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  PRIMARY KEY (learner_id, request_id),
  FOREIGN KEY (lesson_id, lesson_version) REFERENCES lessons(lesson_id, version)
);

-- Durable windows for feedback and reserved reteaching; no provider dispatch here.
CREATE TABLE feedback_operation_deadlines (
  attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES feedback_operations(attempt_id),
  started_at INTEGER NOT NULL CHECK(typeof(started_at) = 'integer' AND started_at >= 0),
  deadline_at INTEGER NOT NULL CHECK(typeof(deadline_at) = 'integer' AND deadline_at = started_at + 180)
);
CREATE TABLE reteaching_operation_deadlines (
  attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES session_routes(attempt_id),
  started_at INTEGER NOT NULL CHECK(typeof(started_at) = 'integer' AND started_at >= 0),
  deadline_at INTEGER NOT NULL CHECK(typeof(deadline_at) = 'integer' AND deadline_at = started_at + 120)
);
