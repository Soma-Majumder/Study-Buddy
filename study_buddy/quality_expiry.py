"""Deadline reconciliation for crashed quality work; never resends model calls.

The batch sweeper is trusted maintenance across learners. The single-operation
entry is owner-scoped and also runs on normal continuation/dispatch requests.
"""
import math
import time

from study_buddy.quality_state import _operation


def _clock(now):
    value = time.time() if now is None else now
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('A finite maintenance timestamp is required.')
    return value


def _fail_deadline_locked(conn, op, now):
    """Internal: caller holds the write lock and has established deadline expiry.

    The one-second pre-dispatch safety margin may also use this terminal save
    when no useful request allowance remains. Published quizzes are untouched.
    """
    result = dict(expired=False, calls_failed=0, sessions_failed=0, retry_operations_failed=0)
    if conn.execute('SELECT 1 FROM quiz_quality_publications WHERE operation_id=?', (op['operation_id'],)).fetchone():
        return result
    if op['stage'] == 'failed' and op['failure_code'] != 'quality_deadline':
        return result
    if op['stage'] != 'failed':
        conn.execute("UPDATE quiz_quality_operations SET stage='failed', failure_code='quality_deadline' WHERE operation_id=?",
                     (op['operation_id'],))
        result['expired'] = True
    result['calls_failed'] = conn.execute(
        "UPDATE quiz_quality_calls SET status='failed', outcome='deadline', finished_at=?, result_fingerprint=NULL "
        "WHERE operation_id=? AND status='reserved'", (now, op['operation_id'])).rowcount
    expected = 'teaching' if op['round'] == 'first' else 'reteaching'
    result['sessions_failed'] = conn.execute(
        "UPDATE study_sessions SET status='failed', failure_reason='quality_deadline' "
        'WHERE session_id=? AND learner_id=? AND status=? AND lesson_id=? AND lesson_version=? '
        'AND learning_retry_count=? AND NOT EXISTS (SELECT 1 FROM quizzes WHERE session_id=? AND round=?)',
        (op['session_id'], op['learner_id'], expected, op['lesson_id'], op['lesson_version'],
         0 if op['round'] == 'first' else 1, op['session_id'], op['round'])).rowcount
    if op['round'] == 'retry':
        result['retry_operations_failed'] = conn.execute(
            "UPDATE retry_quiz_operations SET status='failed', error='quality_deadline' "
            "WHERE session_id=? AND handoff_id=? AND status='running' AND quiz_id IS NULL",
            (op['session_id'], op['handoff_id'])).rowcount
    return result


def _expire_locked(conn, op, now):
    if now < op['deadline_at']:
        return dict(expired=False, calls_failed=0, sessions_failed=0, retry_operations_failed=0)
    return _fail_deadline_locked(conn, op, now)


def expire_quality_operation(conn, learner_id, operation_id, *, now=None):
    """Reconcile one owner's operation atomically; published/other failures stay intact."""
    timestamp = _clock(now)
    with conn:
        op = _operation(conn, learner_id, operation_id)
        if now is None: timestamp = time.time()
        return _expire_locked(conn, op, timestamp)


def sweep_expired_quality_operations(conn, *, now=None, limit=100):
    """Trusted bounded maintenance, including operations nobody revisits.

    Run periodically from the backend scheduler/maintenance command. All status
    updates in a batch commit together; database failures propagate and roll back.
    The caller must not expose cross-learner maintenance to learner input.
    """
    timestamp = _clock(now)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Sweep limit must be an integer from 1 to 1000.')
    with conn:
        # Acquire the write lock before choosing a batch, so publication/completion
        # cannot race this reconciliation. No transaction spans a model call.
        conn.execute('UPDATE quiz_quality_operations SET stage=stage WHERE 0')
        if now is None: timestamp = time.time()
        rows = conn.execute(
            "SELECT o.operation_id, o.learner_id FROM quiz_quality_operations o WHERE o.deadline_at<=? "
            "AND NOT EXISTS (SELECT 1 FROM quiz_quality_publications p WHERE p.operation_id=o.operation_id) "
            "AND (o.stage<>'failed' OR (o.failure_code='quality_deadline' AND ("
            "EXISTS (SELECT 1 FROM quiz_quality_calls c WHERE c.operation_id=o.operation_id AND c.status='reserved') OR "
            "EXISTS (SELECT 1 FROM study_sessions s WHERE s.session_id=o.session_id AND s.learner_id=o.learner_id AND s.lesson_id=o.lesson_id AND s.lesson_version=o.lesson_version AND s.status=CASE WHEN o.round='first' THEN 'teaching' ELSE 'reteaching' END AND s.learning_retry_count=CASE WHEN o.round='first' THEN 0 ELSE 1 END AND NOT EXISTS (SELECT 1 FROM quizzes q WHERE q.session_id=o.session_id AND q.round=o.round)) OR "
            "EXISTS (SELECT 1 FROM retry_quiz_operations r WHERE r.session_id=o.session_id AND r.handoff_id=o.handoff_id AND r.status='running')))) "
            'ORDER BY o.deadline_at, o.operation_id LIMIT ?', (timestamp, limit)).fetchall()
        totals = dict(operations_checked=len(rows), operations_expired=0, calls_failed=0, sessions_failed=0, retry_operations_failed=0)
        for operation_id, learner_id in rows:
            op = _operation(conn, learner_id, operation_id)
            result = _expire_locked(conn, op, timestamp)
            totals['operations_expired'] += int(result.pop('expired'))
            for key, value in result.items():
                totals[key] += value
        return totals
