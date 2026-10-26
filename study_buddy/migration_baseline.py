"""Read-only baseline eligibility for the current complete SQLite schema.

This preview never creates tracking records and never applies migrations.
Only the current full schema through 008 is supported; partial adoption needs
an explicitly reviewed historical reference, not table-name inference.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from study_buddy.migrations import (discover_migrations, initialize_migration_tracking, list_applied_migrations,
                                   MigrationTrackingRejected)

ROOT = Path(__file__).resolve().parent.parent
BASELINE_VERSION = 8
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
    if (type(through_version) is not int or through_version != BASELINE_VERSION or
            not files or files[-1]['version'] != BASELINE_VERSION):
        raise MigrationTrackingRejected('Only the reviewed complete schema through migration 008 can be baselined.')
    tracking = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    if tracking and list_applied_migrations(conn):
        raise MigrationTrackingRejected('This database already has recorded migration history; use normal migration status.')
    with closing(sqlite3.connect(':memory:')) as reference:
        reference.executescript((ROOT/'db/schema.sql').read_text())
        expected = _schema_objects(reference)
        if tracking:
            initialize_migration_tracking(reference)
            expected_tracking = reference.execute("SELECT sql FROM sqlite_master WHERE name='schema_migrations'").fetchone()[0]
            actual_tracking = conn.execute("SELECT sql FROM sqlite_master WHERE name='schema_migrations'").fetchone()[0]
            if _sql_tokens(actual_tracking) != _sql_tokens(expected_tracking):
                raise MigrationTrackingRejected('The existing empty tracking table differs from the reviewed metadata schema.')
    actual = _schema_objects(conn)
    if actual != expected:
        raise MigrationTrackingRejected('Database schema does not exactly match the reviewed full schema through 008.')
    # Checks work without changing the target connection's foreign_keys setting.
    if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise MigrationTrackingRejected('Database has foreign-key violations; repair them before baselining.')
    if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise MigrationTrackingRejected('Database integrity check failed; repair it before baselining.')
    fingerprint = hashlib.sha256(json.dumps(actual, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return dict(status='baseline_ready', through_version=BASELINE_VERSION,
                schema_sha256=fingerprint, would_record=[{key:m[key] for key in ('version','filename','sha256')} for m in files],
                records_written=0)
