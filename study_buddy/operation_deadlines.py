"""Persist operation timing inside the same transaction as its dispatch claim.

Controller reconciliation fences expired work; this module never dispatches agents.
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


def check_operation_deadline_locked(conn, kind, learner_id, attempt_id, *, now=None):
    """Reconcile under the caller's write lock; return permission to continue.

    Completion is preserved. Expiry fails only the matching active session and
    claim, without modifying scores, retry counts, timing or repair budgets.
    """
    import json
    import math
    if not conn.in_transaction:
        raise ValueError('Deadline reconciliation requires a write transaction.')
    timestamp = time.time() if now is None else now
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
        raise ValueError('A finite operation timestamp is required.')
    if kind == 'feedback':
        row = conn.execute(
            'SELECT s.session_id,s.status,f.status,d.deadline_at FROM feedback_operations f '
            'JOIN attempts a ON a.attempt_id=f.attempt_id JOIN quizzes q ON q.quiz_id=a.quiz_id '
            'JOIN study_sessions s ON s.session_id=q.session_id '
            'LEFT JOIN feedback_operation_deadlines d ON d.attempt_id=f.attempt_id '
            'WHERE f.attempt_id=? AND a.learner_id=? AND s.learner_id=?',
            (attempt_id, learner_id, learner_id)).fetchone()
        if not row:
            raise ValueError('Feedback operation is unavailable.')
        sid, status, op_status, deadline = row
        if op_status != 'running' or status != 'grading':
            return False
    elif kind == 'reteaching':
        row = conn.execute(
            'SELECT s.session_id,s.status,r.status,d.deadline_at,r.context,s.learning_retry_count '
            'FROM session_routes r JOIN study_sessions s ON s.session_id=r.session_id '
            'JOIN attempts a ON a.attempt_id=r.attempt_id '
            'LEFT JOIN reteaching_operation_deadlines d ON d.attempt_id=r.attempt_id '
            "WHERE r.attempt_id=? AND r.route='reteach' AND s.learner_id=? AND a.learner_id=?",
            (attempt_id, learner_id, learner_id)).fetchone()
        if not row:
            raise ValueError('Reteaching operation is unavailable.')
        sid, status, op_status, deadline, context_json, retry_count = row
        saved = json.loads(context_json)
        if op_status not in ('reserved', 'dispatched') or status != 'reteaching' or retry_count != 1 or 'tutor_handoff' in saved:
            return False
        if conn.execute("SELECT 1 FROM quiz_quality_operations WHERE session_id=? AND round='retry'", (sid,)).fetchone():
            return False
    else:
        raise ValueError('Unknown timed operation.')
    latest = conn.execute('SELECT a.attempt_id FROM attempts a JOIN quizzes q ON q.quiz_id=a.quiz_id '
                          'WHERE q.session_id=? ORDER BY a.rowid DESC LIMIT 1', (sid,)).fetchone()
    if latest != (attempt_id,):
        return False
    if deadline is None:
        raise ValueError('Saved operation deadline is unavailable.')
    if timestamp < deadline:
        return True
    code = kind + '_deadline'
    if kind == 'feedback':
        conn.execute("UPDATE feedback_operations SET status='failed' WHERE attempt_id=? AND status='running'", (attempt_id,))
    else:
        saved['retry_failure'] = 'Reteaching deadline expired. The learning retry stays consumed.'
        conn.execute("UPDATE session_routes SET status='failed',context=? WHERE attempt_id=?", (json.dumps(saved), attempt_id))
    conn.execute("UPDATE study_sessions SET status='failed',failure_reason=? WHERE session_id=? AND learner_id=?",
                 (code, sid, learner_id))
    return False
