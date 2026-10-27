-- Deduplicate client Start requests before any paid agent dispatch.
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
