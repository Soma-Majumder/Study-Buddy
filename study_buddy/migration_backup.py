"""Owner-only SQLite backups that never overwrite and never leave partial files."""
from contextlib import closing
from pathlib import Path
import sqlite3


def _copy(source_file, destination):
    # A connection holding its own write lock cannot back itself up (SQLite
    # reports it busy forever), but a separate reader can read the file while the
    # writer holds the lock, and the lock keeps new commits out of the copy.
    with closing(sqlite3.connect(Path(source_file).resolve().as_uri() + '?mode=ro', uri=True)) as source:
        source.backup(destination)


def write_backup(conn, path):
    """Back up conn's main database to a new file; remove it if the backup fails.

    Call while holding the write lock (BEGIN IMMEDIATE) so no committed write can
    fall outside the backup. Exclusive creation refuses existing paths, including
    symlinks. Never use for in-memory databases.
    """
    source_file = next((row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main'), '')
    if not source_file:
        raise sqlite3.OperationalError('Only file-backed databases can be backed up.')
    path = Path(path)
    created = False
    try:
        with path.open('xb'):
            created = True
        path.chmod(0o600)
        with closing(sqlite3.connect(path)) as destination:
            _copy(source_file, destination)
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise
