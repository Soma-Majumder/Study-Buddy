"""Migration tracking only: this module never executes application migration SQL."""
from contextlib import nullcontext
import hashlib
from pathlib import Path
import re

TRACKING_SQL = (Path(__file__).resolve().parent.parent / 'db/migration_tracking.sql').read_text()
NAME = re.compile(r'^([0-9]{3})_[a-z][a-z0-9_]*\.sql$')


class MigrationTrackingRejected(ValueError):
    """Invalid metadata or a previously recorded version/file was changed."""


def initialize_migration_tracking(conn):
    """Create tracking metadata without inferring history or committing caller work."""
    # One statement, deliberately not executescript (which can commit caller work).
    conn.execute(TRACKING_SQL)


def list_applied_migrations(conn):
    """Read recorded history in version order; initialize tracking first."""
    rows = conn.execute('SELECT version, filename, sha256, applied_at FROM schema_migrations ORDER BY version').fetchall()
    return [dict(zip(('version', 'filename', 'sha256', 'applied_at'), row)) for row in rows]


def record_applied_migration(conn, filename, sql_bytes, *, manage_transaction=True):
    """Record a trusted caller's successful migration, never run or verify its SQL.

    Hash the exact file bytes, without newline/whitespace normalization. The
    future runner must execute SQL and record it in the SAME transaction with
    manage_transaction=False. This helper cannot prove a migration was applied.
    Never baseline an existing/fresh-schema database merely from table presence.
    """
    match = NAME.fullmatch(filename) if isinstance(filename, str) else None
    if not match or int(match[1]) == 0 or not isinstance(sql_bytes, bytes) or not sql_bytes.strip():
        raise MigrationTrackingRejected('A numbered SQL filename and nonempty exact file bytes are required.')
    version = int(match[1])
    digest = hashlib.sha256(sql_bytes).hexdigest()
    with conn if manage_transaction else nullcontext():
        # Serialize reads/inserts for concurrent callers; no model/network access.
        conn.execute('UPDATE schema_migrations SET version=version WHERE 0')
        previous = conn.execute('SELECT filename, sha256 FROM schema_migrations WHERE version=?', (version,)).fetchone()
        if previous and previous != (filename, digest):
            raise MigrationTrackingRejected('Recorded migration filename or SQL fingerprint changed.')
        recorded = previous is None
        if recorded:
            conn.execute('INSERT INTO schema_migrations (version, filename, sha256) VALUES (?, ?, ?)',
                         (version, filename, digest))
        row = conn.execute('SELECT version, filename, sha256, applied_at FROM schema_migrations WHERE version=?',
                           (version,)).fetchone()
        return dict(zip(('version', 'filename', 'sha256', 'applied_at'), row), recorded=recorded)
