"""Persist operation timing inside the same transaction as its dispatch claim.

Expiry and late-result fencing are separate; this module never dispatches agents.
"""
import time

FEEDBACK_WINDOW_SECONDS = 180
RETEACHING_WINDOW_SECONDS = 120


def record_operation_deadline(conn, kind, attempt_id):
    """Called only for a newly created claim; INSERT never resets an old window."""
    if not conn.in_transaction:
        raise ValueError('Operation timing requires the claim transaction.')
    if kind == 'feedback':
        table, duration = 'feedback_operation_deadlines', FEEDBACK_WINDOW_SECONDS
    elif kind == 'reteaching':
        table, duration = 'reteaching_operation_deadlines', RETEACHING_WINDOW_SECONDS
    else:
        raise ValueError('Unknown timed operation.')
    started_at = int(time.time())
    conn.execute(f'INSERT INTO {table} (attempt_id, started_at, deadline_at) VALUES (?, ?, ?)',
                 (attempt_id, started_at, started_at + duration))
