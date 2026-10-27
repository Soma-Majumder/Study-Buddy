"""Local request reservation used by explicitly opted-in controller starts."""
import re
import math
import time
from datetime import datetime, timezone

from study_buddy.answer_keys import new_id


class StartRequestRejected(ValueError):
    pass


def reserve_session_start(conn, learner_id, request_id, lesson_id, lesson_version):
    """Durably reserve exactly one start for a learner/request ID and fixed inputs.

    Returns reserved=True only to the caller that inserts the record. Every
    replay returns reserved=False and current saved session status, never a new
    dispatch grant. No agents run here; crashes do not release or refresh claims.
    The caller must use a connection without pending work: commit precedes any
    eventual agent call. Migration 009 or the full fresh schema must be installed first.
    """
    if conn.in_transaction:
        raise StartRequestRejected('Session-start reservation requires no pending caller work.')
    if not isinstance(learner_id,str) or not re.fullmatch(r'learner_[a-z0-9]{12}',learner_id):
        raise StartRequestRejected('A trusted learner ID is required.')
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{7,127}',request_id):
        raise StartRequestRejected('Request ID must contain 8–128 ASCII letters, digits, underscores or hyphens.')
    if not isinstance(lesson_id,str) or not re.fullmatch(r'[a-z][a-z0-9_]*',lesson_id) or type(lesson_version) is not int or lesson_version < 1:
        raise StartRequestRejected('An explicit lesson ID and positive integer version are required.')
    if conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise StartRequestRejected('Foreign-key enforcement must be enabled.')
    conn.execute('BEGIN IMMEDIATE')
    try:
        if not conn.execute('SELECT 1 FROM learners WHERE learner_id=?',(learner_id,)).fetchone():
            raise StartRequestRejected('Learner is not registered.')
        existing=conn.execute('SELECT lesson_id,lesson_version,session_id,handoff_id,explanation_id,created_at '
                              'FROM session_start_requests WHERE learner_id=? AND request_id=?',
                              (learner_id,request_id)).fetchone()
        if existing:
            if existing[:2] != (lesson_id,lesson_version):
                raise StartRequestRejected('This request ID was already used with different lesson inputs.')
            session=conn.execute('SELECT status,lesson_id,lesson_version FROM study_sessions WHERE session_id=? AND learner_id=?',
                                 (existing[2],learner_id)).fetchone()
            if not session or session[1:] != existing[:2]:
                raise StartRequestRejected('Saved session-start evidence is inconsistent.')
            check_session_start_deadline_locked(conn, learner_id, existing[2])
            session=conn.execute('SELECT status,lesson_id,lesson_version FROM study_sessions WHERE session_id=? AND learner_id=?',
                                 (existing[2],learner_id)).fetchone()
            result=dict(reserved=False,request_id=request_id,lesson_id=lesson_id,lesson_version=lesson_version,
                        session_id=existing[2],handoff_id=existing[3],explanation_id=existing[4],
                        created_at=existing[5],status=session[0])
        else:
            if not conn.execute('SELECT 1 FROM lessons WHERE lesson_id=? AND version=? AND approved=1',
                                (lesson_id,lesson_version)).fetchone():
                raise StartRequestRejected('An approved lesson version is required.')
            sid,hid,eid=new_id('session'),new_id('handoff'),new_id('expl')
            created_at=conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
            conn.execute("INSERT INTO study_sessions (session_id,learner_id,lesson_id,lesson_version,status,created_at) VALUES (?,?,?,?,'teaching',?)",
                         (sid,learner_id,lesson_id,lesson_version,created_at))
            conn.execute("INSERT INTO handoff_repairs VALUES (?,?,'tutor_to_assessment',0)",(hid,sid))
            conn.execute('INSERT INTO session_start_requests VALUES (?,?,?,?,?,?,?,?)',
                         (learner_id,request_id,lesson_id,lesson_version,sid,hid,eid,created_at))
            result=dict(reserved=True,request_id=request_id,lesson_id=lesson_id,lesson_version=lesson_version,
                        session_id=sid,handoff_id=hid,explanation_id=eid,created_at=created_at,status='teaching')
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


# Protocol policy for migration-009 reservations. Keep this fixed for existing
# claims: changing the duration requires a versioned/persisted policy.
SESSION_START_WINDOW_SECONDS = 120


def check_session_start_deadline_locked(conn, learner_id, session_id, *, now=None):
    """Caller owns a write transaction; reconcile before dispatch/result save.

    The immutable reservation timestamp defines the deadline. Once a first-round
    quality operation exists it owns expiry instead. Never releases a claim.
    """
    if not conn.in_transaction:
        raise StartRequestRejected('Deadline checks require a write transaction.')
    timestamp = time.time() if now is None else now
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
        raise StartRequestRejected('A finite deadline timestamp is required.')
    row = conn.execute(
        'SELECT r.created_at,s.status FROM session_start_requests r JOIN study_sessions s '
        'ON s.session_id=r.session_id AND s.learner_id=r.learner_id '
        'AND s.lesson_id=r.lesson_id AND s.lesson_version=r.lesson_version '
        'WHERE r.session_id=? AND r.learner_id=?', (session_id, learner_id)).fetchone()
    if not row:
        raise StartRequestRejected('Reserved session is unavailable.')
    if row[1] != 'teaching' or conn.execute('SELECT 1 FROM quizzes WHERE session_id=?', (session_id,)).fetchone():
        return False
    if conn.execute("SELECT 1 FROM quiz_quality_operations WHERE session_id=? AND round='first'",
                    (session_id,)).fetchone():
        return False
    try:
        created = datetime.strptime(row[0], '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        raise StartRequestRejected('Reserved session timestamp is invalid.') from None
    if timestamp >= created + SESSION_START_WINDOW_SECONDS:
        conn.execute("UPDATE study_sessions SET status='failed', failure_reason='session_start_deadline' "
                     "WHERE session_id=? AND learner_id=? AND status='teaching'", (session_id, learner_id))
        return False
    return True


def sweep_expired_session_starts(conn, *, now=None, limit=100):
    """Expire a bounded batch of abandoned starts, never redispatching Tutor.

    Owns its write transaction. Quality operations own their own deadlines;
    completed, published, legacy and other terminal sessions are untouched.
    """
    if conn.in_transaction:
        raise StartRequestRejected('Start expiry requires no pending caller work.')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise StartRequestRejected('Sweep limit must be an integer from 1 to 1000.')
    timestamp = time.time() if now is None else now
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
        raise StartRequestRejected('A finite deadline timestamp is required.')
    with conn:
        conn.execute('UPDATE study_sessions SET status=status WHERE 0')
        # Sample after acquiring the lock so lock waiting cannot extend a claim.
        if now is None:
            timestamp = time.time()
        cutoff = datetime.fromtimestamp(timestamp - SESSION_START_WINDOW_SECONDS, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        rows = conn.execute(
            'SELECT r.learner_id,r.session_id FROM session_start_requests r '
            'JOIN study_sessions s ON s.session_id=r.session_id AND s.learner_id=r.learner_id '
            'AND s.lesson_id=r.lesson_id AND s.lesson_version=r.lesson_version '
            "WHERE s.status='teaching' AND r.created_at<=? "
            'AND NOT EXISTS (SELECT 1 FROM quizzes q WHERE q.session_id=r.session_id) '
            "AND NOT EXISTS (SELECT 1 FROM quiz_quality_operations o WHERE o.session_id=r.session_id AND o.round='first') "
            'ORDER BY r.created_at,r.session_id LIMIT ?', (cutoff, limit)).fetchall()
        expired = 0
        for learner_id, session_id in rows:
            check_session_start_deadline_locked(conn, learner_id, session_id, now=timestamp)
            expired += int(conn.execute(
                "SELECT 1 FROM study_sessions WHERE session_id=? AND status='failed' AND failure_reason='session_start_deadline'",
                (session_id,)).fetchone() is not None)
        return dict(session_starts_checked=len(rows), session_starts_expired=expired)
