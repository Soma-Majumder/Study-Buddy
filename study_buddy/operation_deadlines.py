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


def sweep_expired_agent_operations(conn, kind, *, now=None, limit=100):
    """Trusted maintenance: one atomic bounded batch, never a provider dispatch.

    Missing timing on otherwise eligible active work fails explicitly rather
    than hiding it forever. Ready work and work owned by quiz preparation stay
    outside this sweep. The caller must not have pending work.
    """
    import math
    if conn.in_transaction:
        raise ValueError('Operation expiry requires no pending caller work.')
    if kind not in ('feedback', 'reteaching'):
        raise ValueError('Unknown timed operation.')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Sweep limit must be an integer from 1 to 1000.')
    timestamp = time.time() if now is None else now
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
        raise ValueError('A finite operation timestamp is required.')
    with conn:
        conn.execute('UPDATE study_sessions SET status=status WHERE 0')
        if now is None:
            timestamp = time.time()
        if kind == 'feedback':
            query = (
                'SELECT f.attempt_id,s.learner_id FROM feedback_operations f '
                'JOIN attempts a ON a.attempt_id=f.attempt_id JOIN quizzes q ON q.quiz_id=a.quiz_id '
                'JOIN study_sessions s ON s.session_id=q.session_id AND s.learner_id=a.learner_id '
                'LEFT JOIN feedback_operation_deadlines d ON d.attempt_id=f.attempt_id '
                "WHERE f.status='running' AND s.status='grading' ")
        else:
            query = (
                'SELECT r.attempt_id,s.learner_id FROM session_routes r '
                'JOIN study_sessions s ON s.session_id=r.session_id '
                'JOIN attempts a ON a.attempt_id=r.attempt_id AND a.learner_id=s.learner_id '
                'LEFT JOIN reteaching_operation_deadlines d ON d.attempt_id=r.attempt_id '
                "WHERE r.route='reteach' AND r.status IN ('reserved','dispatched') "
                "AND s.status='reteaching' AND s.learning_retry_count=1 "
                "AND json_type(r.context,'$.tutor_handoff') IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM quiz_quality_operations o WHERE o.session_id=s.session_id AND o.round='retry') ")
        query += (
            'AND a.attempt_id=(SELECT latest.attempt_id FROM attempts latest '
            'JOIN quizzes lq ON lq.quiz_id=latest.quiz_id WHERE lq.session_id=s.session_id '
            'ORDER BY latest.rowid DESC LIMIT 1) '
            'AND (d.deadline_at<=? OR d.attempt_id IS NULL) '
            'ORDER BY d.deadline_at,a.attempt_id LIMIT ?')
        rows = conn.execute(query, (timestamp, limit)).fetchall()
        # Every selected record is active and overdue; the shared checker makes
        # the operation/session updates together under this same write lock.
        for attempt_id, learner_id in rows:
            check_operation_deadline_locked(conn, kind, learner_id, attempt_id, now=timestamp)
        return {kind + '_operations_checked': len(rows), kind + '_operations_expired': len(rows)}
