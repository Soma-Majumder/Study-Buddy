"""Baseline preview and explicit atomic adoption for the complete SQLite schema.

The preview never creates tracking records and never applies migrations.
Only reviewed full schemas through 008 and 009 are supported. Historical 008
is frozen separately; adoption is never inferred from table names.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from study_buddy.migrations import (discover_migrations, initialize_migration_tracking, list_applied_migrations,
                                   record_applied_migration, MigrationTrackingRejected)

ROOT = Path(__file__).resolve().parent.parent
BASELINE_VERSION = 9
BASELINE_REFERENCES = {8: ROOT/'db/baselines/008_schema.sql', 9: ROOT/'db/schema.sql'}
# Tokenize quotes before interpreting their contents as comments/whitespace.
# Preserve quoted strings/identifiers exactly; normalize unquoted case/spacing.
TOKENS = re.compile(r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]|[A-Za-z_][A-Za-z_0-9]*|\s+|.")


def _sql_tokens(sql):
    return tuple(token if token[:1] in ("'", '"', '`', '[') else token.casefold()
                 for token in TOKENS.findall(sql)
                 if not token.isspace() and not token.startswith(('--', '/*')))


def _schema_objects(conn):
    rows = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' "
                        "AND tbl_name<>'schema_migrations' ORDER BY type,name").fetchall()
    return [(kind, name, table, _sql_tokens(sql or '')) for kind, name, table, sql in rows]


def plan_baseline(conn, *, through_version):
    """Verify a complete current schema and return a non-authorizing preview.

    Reject tracked history, unknown versions, extra/missing/changed schema
    objects, foreign-key violations and integrity errors. No application data
    is returned. Execute the eventual baseline recheck/recording atomically
    under a write lock; this read-only preview can become stale afterward.
    """
    files = discover_migrations()
    if (type(through_version) is not int or through_version not in BASELINE_REFERENCES or
            not files or files[-1]['version'] != BASELINE_VERSION):
        raise MigrationTrackingRejected('Only the reviewed complete schemas through migration 008 or 009 can be baselined.')
    files = [migration for migration in files if migration['version'] <= through_version]
    tracking = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    if tracking and list_applied_migrations(conn):
        raise MigrationTrackingRejected('This database already has recorded migration history; use normal migration status.')
    with closing(sqlite3.connect(':memory:')) as reference:
        reference.executescript(BASELINE_REFERENCES[through_version].read_text())
        expected = _schema_objects(reference)
        if tracking:
            initialize_migration_tracking(reference)
            expected_tracking = reference.execute("SELECT sql FROM sqlite_master WHERE name='schema_migrations'").fetchone()[0]
            actual_tracking = conn.execute("SELECT sql FROM sqlite_master WHERE name='schema_migrations'").fetchone()[0]
            if _sql_tokens(actual_tracking) != _sql_tokens(expected_tracking):
                raise MigrationTrackingRejected('The existing empty tracking table differs from the reviewed metadata schema.')
    actual = _schema_objects(conn)
    if actual != expected:
        raise MigrationTrackingRejected(f'Database schema does not exactly match the reviewed full schema through {through_version:03d}.')
    # Checks work without changing the target connection's foreign_keys setting.
    if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise MigrationTrackingRejected('Database has foreign-key violations; repair them before baselining.')
    if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise MigrationTrackingRejected('Database integrity check failed; repair it before baselining.')
    fingerprint = hashlib.sha256(json.dumps(actual, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    manifest = [{key:m[key] for key in ('version','filename','sha256')} for m in files]
    return dict(status='baseline_ready', through_version=through_version,
                schema_sha256=fingerprint, manifest_sha256=_manifest_hash(manifest), would_record=manifest,
                records_written=0)


def _manifest_hash(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def record_verified_baseline(conn, *, through_version, expected_schema_sha256, expected_manifest_sha256,
                             recorded_by, reason):
    """Explicit adoption: lock, reverify approved preview, record all or none.

    Owns its transaction; reject a caller's pending transaction instead of
    committing it. The operator must take a backup first (the CLI does so).
    Existing recorded history is rejected, never replaced or backfilled.
    """
    if conn.in_transaction:
        raise MigrationTrackingRejected('Baseline recording requires a connection without pending work.')
    if (any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
            for value in (expected_schema_sha256, expected_manifest_sha256)) or
            not isinstance(recorded_by, str) or not recorded_by.strip() or len(recorded_by)>200 or
            not isinstance(reason, str) or not reason.strip() or len(reason)>2000):
        raise MigrationTrackingRejected('Explicit preview fingerprints, operator and reason are required.')
    conn.execute('BEGIN IMMEDIATE')
    try:
        preview = plan_baseline(conn, through_version=through_version)
        if (preview['schema_sha256'], preview['manifest_sha256']) != (expected_schema_sha256, expected_manifest_sha256):
            raise MigrationTrackingRejected('Database schema or migration files changed since the approved preview.')
        files = [migration for migration in discover_migrations() if migration['version'] <= through_version]
        manifest = [{key:m[key] for key in ('version','filename','sha256')} for m in files]
        if _manifest_hash(manifest) != expected_manifest_sha256:
            raise MigrationTrackingRejected('Migration files changed during baseline verification.')
        initialize_migration_tracking(conn)
        conn.execute((ROOT/'db/migration_baseline_audit.sql').read_text())
        for migration in files:
            record_applied_migration(conn, migration['filename'], migration['sql_bytes'], manage_transaction=False)
        conn.execute('INSERT INTO schema_baselines '
                     '(baseline_id, through_version, schema_sha256, manifest_sha256, migration_manifest, recorded_by, reason) '
                     'VALUES (1, ?, ?, ?, ?, ?, ?)',
                     (through_version, expected_schema_sha256, expected_manifest_sha256,
                      json.dumps(manifest, sort_keys=True, separators=(',', ':')), recorded_by.strip(), reason.strip()))
        recorded_at = conn.execute('SELECT recorded_at FROM schema_baselines WHERE baseline_id=1').fetchone()[0]
        conn.commit()
        return dict(status='baselined', through_version=through_version, records_written=len(files),
                    schema_sha256=expected_schema_sha256, manifest_sha256=expected_manifest_sha256, recorded_at=recorded_at)
    except BaseException:
        conn.rollback()
        raise
