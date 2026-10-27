"""Local request reservation used by explicitly opted-in controller starts."""
import re

from study_buddy.answer_keys import new_id


class StartRequestRejected(ValueError):
    pass


def reserve_session_start(conn, learner_id, request_id, lesson_id, lesson_version):
    """Durably reserve exactly one start for a learner/request ID and fixed inputs.

    Returns reserved=True only to the caller that inserts the record. Every
    replay returns reserved=False and current saved session status, never a new
    dispatch grant. No agents run here; crashes do not release or refresh claims.
    The caller must use a connection without pending work: commit precedes any
    eventual agent call. The proposed table must be installed explicitly first.
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
