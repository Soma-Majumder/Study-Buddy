"""Trusted maintenance entry point: one bounded cycle, with its own connection."""
from contextlib import closing
from pathlib import Path
import sqlite3

from study_buddy.quality_expiry import sweep_expired_quality_operations


def run_maintenance_cycle(database, *, limit=100):
    """Expire one batch and return counts only; failures propagate to the caller.

    Opens an existing database without creating one, owns/closes its connection,
    and bounds SQLite lock waiting to five seconds. No agent dispatch, retry loop,
    scheduler, schema initialization, or migration execution happens here.
    The database path and batch limit are trusted operator configuration.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Sweep limit must be an integer from 1 to 1000.')
    uri = Path(database).resolve().as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        return sweep_expired_quality_operations(conn, limit=limit)
