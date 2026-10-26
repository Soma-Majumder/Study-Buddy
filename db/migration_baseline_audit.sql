-- Bootstrap audit for explicit adoption; never represents historical execution.
CREATE TABLE schema_baselines (
  baseline_id INTEGER PRIMARY KEY CHECK (baseline_id = 1),
  through_version INTEGER NOT NULL CHECK (through_version > 0),
  schema_sha256 TEXT NOT NULL CHECK (length(schema_sha256) = 64),
  manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
  migration_manifest TEXT NOT NULL,
  recorded_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
