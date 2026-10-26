"""Migration tracking only: this module never executes application migration SQL."""
from contextlib import nullcontext
import hashlib
import json
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


DEFAULT_MIGRATIONS = Path(__file__).resolve().parent.parent / 'db/migrations'


def discover_migrations(directory=DEFAULT_MIGRATIONS):
    """Load exact SQL bytes in contiguous version order; execute nothing.

    The directory is trusted backend configuration, never a learner path.
    Ignore non-SQL notes, but reject SQL-looking names, symlinks and duplicate
    or missing versions so a broken directory cannot silently change the plan.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise MigrationTrackingRejected('Migration directory is unavailable.')
    migrations = []
    versions = set()
    for path in directory.iterdir():
        if path.suffix.lower() != '.sql':
            continue
        match = NAME.fullmatch(path.name)
        if not match or int(match[1]) == 0 or path.is_symlink() or not path.is_file():
            raise MigrationTrackingRejected('Migration SQL files need valid numbered names and must be regular files.')
        version = int(match[1])
        if version in versions:
            raise MigrationTrackingRejected('Two migration files use the same version.')
        data = path.read_bytes()
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            raise MigrationTrackingRejected('Migration SQL must be UTF-8 text.') from None
        if not text.strip():
            raise MigrationTrackingRejected('Migration SQL cannot be empty.')
        versions.add(version)
        migrations.append(dict(version=version, filename=path.name,
                               sha256=hashlib.sha256(data).hexdigest(), sql_bytes=data))
    migrations.sort(key=lambda migration: migration['version'])
    if [m['version'] for m in migrations] != list(range(1, len(migrations) + 1)):
        raise MigrationTrackingRejected('Migration files must form a continuous sequence starting at 001.')
    return migrations


def plan_migrations(conn, directory=DEFAULT_MIGRATIONS):
    """Read-only discovery/history comparison, NOT permission to execute a plan.

    Pending means unrecorded. Existing application tables without history need
    an explicit baseline decision; do not assume the pending SQL is unapplied.
    Recheck all files/history inside the future runner's write transaction.
    """
    files = discover_migrations(directory)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*'")}
    applied = list_applied_migrations(conn) if 'schema_migrations' in tables else []
    by_version = {m['version']: m for m in files}
    for record in applied:
        source = by_version.get(record['version'])
        if source is None:
            raise MigrationTrackingRejected('A recorded migration file is missing.')
        if (record['filename'], record['sha256']) != (source['filename'], source['sha256']):
            raise MigrationTrackingRejected('Recorded migration filename or SQL fingerprint differs from disk.')
    if [r['version'] for r in applied] != list(range(1, len(applied) + 1)):
        raise MigrationTrackingRejected('Recorded history is not a continuous prefix of the migration files.')
    recorded_versions = {r['version'] for r in applied}
    pending = [{key: m[key] for key in ('version', 'filename', 'sha256')}
               for m in files if m['version'] not in recorded_versions]
    baseline_required = bool(tables - {'schema_migrations'}) and not applied
    return dict(status='baseline_required' if baseline_required else 'pending' if pending else 'up_to_date',
                baseline_required=baseline_required, tracking_initialized='schema_migrations' in tables,
                applied=applied, pending=pending, pending_sha256=migration_manifest_hash(pending))


def migration_manifest_hash(manifest):
    """Stable approval fingerprint for ordered filename/version/content metadata."""
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
