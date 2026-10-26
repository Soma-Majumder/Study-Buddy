"""Trusted maintenance entry point: one bounded cycle, with its own connection."""
from contextlib import closing
from pathlib import Path
import sqlite3
import math
from threading import Event

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


def run_maintenance_worker(database, *, limit=100, interval_seconds=30,
                           stop_event=None, on_cycle=None):
    """Run serial cycles until stopped; errors propagate without another cycle.

    Wait after each completed cycle (no overlapping sweeps or catch-up bursts).
    Event-based waiting wakes immediately on shutdown. A stop during a cycle
    lets that bounded batch commit/roll back and close its connection first.
    The caller owns signal handling; optional on_cycle receives counts only.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Sweep limit must be an integer from 1 to 1000.')
    if type(interval_seconds) not in (int, float) or not math.isfinite(interval_seconds) or not 0 < interval_seconds <= 86400:
        raise ValueError('Worker interval must be finite and greater than zero, at most 86400 seconds.')
    stop = Event() if stop_event is None else stop_event
    cycles = 0
    while not stop.is_set():
        counts = run_maintenance_cycle(database, limit=limit)
        cycles += 1
        if on_cycle is not None:
            on_cycle(counts)
        if stop.is_set() or stop.wait(interval_seconds):
            break
    return dict(status='stopped', cycles_completed=cycles)
