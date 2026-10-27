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

-- Legacy active work has no original timestamp. Give it one adoption window;
-- these timestamps mean migration adoption, not historical provider start time.
INSERT INTO feedback_operation_deadlines (attempt_id, started_at, deadline_at)
SELECT f.attempt_id, CAST(strftime('%s','now') AS INTEGER), CAST(strftime('%s','now') AS INTEGER) + 180
FROM feedback_operations f
JOIN attempts a ON a.attempt_id=f.attempt_id JOIN quizzes q ON q.quiz_id=a.quiz_id
JOIN study_sessions s ON s.session_id=q.session_id
WHERE f.status='running' AND s.status='grading';
INSERT INTO reteaching_operation_deadlines (attempt_id, started_at, deadline_at)
SELECT r.attempt_id, CAST(strftime('%s','now') AS INTEGER), CAST(strftime('%s','now') AS INTEGER) + 120
FROM session_routes r JOIN study_sessions s ON s.session_id=r.session_id
WHERE r.route='reteach' AND r.status IN ('reserved','dispatched') AND s.status='reteaching';
