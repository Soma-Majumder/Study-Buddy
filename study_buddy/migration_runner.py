"""Apply reviewed pending SQLite migrations and history in one transaction."""
import re
import sqlite3
from pathlib import Path

from study_buddy.migrations import (DEFAULT_MIGRATIONS, discover_migrations, plan_migrations,
    initialize_migration_tracking, record_applied_migration, migration_manifest_hash, MigrationTrackingRejected)

METADATA = {'schema_migrations', 'schema_baselines'}


class MigrationExecutionFailed(RuntimeError):
    def __init__(self, filename, stage, statement_number=None):
        location = f' (statement {statement_number})' if statement_number is not None else ''
        super().__init__(f'Migration {filename} failed during {stage}{location}; the migration batch was rolled back.')
        self.filename = filename
        self.stage = stage
        self.statement_number = statement_number


def _statements(sql):
    """Use SQLite's completeness parser; semicolons inside strings aren't splits."""
    buffer = ''
    for character in sql:
        buffer += character
        if character == ';' and sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ''
    if buffer.strip():
        # SQLite accepts trailing comments and final statements without ';'.
        # Incomplete/multiple SQL is rejected by execute inside the transaction.
        yield buffer


def _migration_authorizer(action, first, second, database, source):
    # These migrations are reviewed code, not an untrusted SQL sandbox. Prevent
    # scripts from escaping transaction ownership or rewriting tracking metadata.
    first = first.casefold() if isinstance(first, str) else first
    second = second.casefold() if isinstance(second, str) else second
    denied = {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_ATTACH,
              sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA, sqlite3.SQLITE_CREATE_TRIGGER,
              sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_TABLE,
              sqlite3.SQLITE_CREATE_TEMP_INDEX, sqlite3.SQLITE_CREATE_TEMP_VIEW}
    if action in denied:
        return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                  sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_CREATE_TABLE) and first in METADATA:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_ALTER_TABLE and second in METADATA:
        return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX) and second in METADATA:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and second == 'load_extension':
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _metadata_schema(conn):
    # ALTER TABLE x RENAME TO <metadata name> is not reported to the authorizer
    # for the new name, so compare the protected objects' definitions instead.
    marks = ','.join('?' * len(METADATA))
    return conn.execute(f'SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name IN ({marks}) '
                        f'OR tbl_name IN ({marks}) ORDER BY type,name', (*sorted(METADATA), *sorted(METADATA))).fetchall()


def run_pending_migrations(conn, *, expected_pending_sha256, directory=DEFAULT_MIGRATIONS, before_apply=None):
    """Dedicated maintenance connection only; execute exactly the reviewed plan.

    No pending caller transaction or custom authorizer belongs on this connection.
    before_apply(conn), if given, runs under the write lock after the plan and
    integrity checks pass and before any change (the CLI backs up there). Nonempty untracked databases must be baselined;
    the project's incremental migrations do not bootstrap its original base tables.
    """
    if conn.in_transaction:
        raise MigrationTrackingRejected('Migration execution requires a connection without pending work.')
    if not isinstance(expected_pending_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_pending_sha256):
        raise MigrationTrackingRejected('The pending-plan fingerprint from migration_status.py is required.')
    if conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise MigrationTrackingRejected('Foreign-key enforcement must be enabled on the maintenance connection.')
    conn.execute('BEGIN IMMEDIATE')
    filename, stage, statement_number = 'batch', 'planning', None
    try:
        plan = plan_migrations(conn, directory)
        if plan['baseline_required']:
            raise MigrationTrackingRejected('Existing untracked tables require a verified baseline before applying migrations.')
        if not plan['pending']:
            conn.commit()
            return dict(status='up_to_date', migrations_applied=0, applied=[], pending_sha256=plan['pending_sha256'])
        if plan['pending_sha256'] != expected_pending_sha256:
            raise MigrationTrackingRejected('The pending migration plan changed; review a new status report.')
        if not plan['applied'] and Path(directory).resolve() == DEFAULT_MIGRATIONS.resolve():
            raise MigrationTrackingRejected('Initialize the complete application schema and verify its baseline first; these incremental files do not create the original base tables.')
        # Freeze the file bytes actually executed; detect drift since planning.
        files = discover_migrations(directory)
        pending = files[len(plan['applied']):]
        metadata = [{key:m[key] for key in ('version','filename','sha256')} for m in pending]
        if migration_manifest_hash(metadata) != expected_pending_sha256:
            raise MigrationTrackingRejected('Migration files changed during execution planning.')
        stage = 'preflight integrity checks'
        if conn.execute('PRAGMA foreign_key_check').fetchone() is not None or conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise MigrationTrackingRejected('Database integrity checks failed before migration execution.')
        if before_apply is not None:
            stage = 'backup'
            before_apply(conn)
            stage = 'preflight integrity checks'
        initialize_migration_tracking(conn)
        for migration in pending:
            filename, stage = migration['filename'], 'SQL execution'
            protected = _metadata_schema(conn)
            try:
                conn.set_authorizer(_migration_authorizer)
                for statement_number, statement in enumerate(_statements(migration['sql_bytes'].decode('utf-8')), 1):
                    conn.execute(statement)  # Never executescript: it can commit the outer transaction.
            finally:
                conn.set_authorizer(None)
            stage = 'metadata protection'
            if _metadata_schema(conn) != protected:
                raise MigrationExecutionFailed(filename, stage)
            stage = 'integrity checks'
            if conn.execute('PRAGMA foreign_key_check').fetchone() is not None or conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise MigrationExecutionFailed(filename, stage)
            stage = 'history recording'
            record_applied_migration(conn, migration['filename'], migration['sql_bytes'], manage_transaction=False)
        stage = 'commit'
        conn.commit()
        return dict(status='migrated', migrations_applied=len(metadata), applied=metadata,
                    pending_sha256=migration_manifest_hash([]))
    except BaseException as error:
        conn.rollback()
        if isinstance(error, sqlite3.Error):
            raise MigrationExecutionFailed(filename, stage, statement_number) from None
        raise
