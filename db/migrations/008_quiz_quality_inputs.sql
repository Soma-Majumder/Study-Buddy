-- Persist the validated original Tutor handoff for crash-safe continuation.
CREATE TABLE quiz_quality_inputs (
  operation_id TEXT PRIMARY KEY REFERENCES quiz_quality_operations(operation_id),
  handoff_data TEXT NOT NULL
);
