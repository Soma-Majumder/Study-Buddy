"""Claim a saved learning retry and call Tutor at most once per reservation."""
import copy
import json

from study_buddy.grading import grade
from study_buddy.answer_keys import get_answer_key


class ReteachingRejected(ValueError):
    pass


def dispatch_reteaching(conn, learner_id, attempt_id, *, tutor=None):
    # Lazy import keeps the controller's public re-export free of a cycle.
    from study_buddy.controller import run_tutor, _check_handoff, TutorFailure
    tutor = run_tutor if tutor is None else tutor
    with conn:
        owned = conn.execute(
            'UPDATE study_sessions SET status = status WHERE learner_id = ? AND session_id IN ('
            'SELECT session_id FROM session_routes WHERE attempt_id = ?)', (learner_id, attempt_id),
        ).rowcount
        if not owned:
            raise ReteachingRejected('Retry reservation is unavailable for this learner.')
        row = conn.execute(
            'SELECT sr.session_id, sr.context, sr.status, s.status, s.learning_retry_count, '
            's.lesson_id, s.lesson_version FROM session_routes sr '
            'JOIN study_sessions s ON s.session_id = sr.session_id '
            "WHERE sr.attempt_id = ? AND sr.route = 'reteach' AND s.learner_id = ?",
            (attempt_id, learner_id),
        ).fetchone()
        if row is None:
            raise ReteachingRejected('A saved reteaching reservation is required.')
        sid, context_json, op_status, status, retry_count, lesson_id, version = row
        saved = json.loads(context_json)
        if op_status == 'failed':
            return dict(session_id=sid, operation_status='failed', status=status, next_step=None,
                        handoff=None, error=saved.get('retry_failure', 'Reteaching failed.'))
        if op_status == 'dispatched':
            ready = 'tutor_handoff' in saved
            return dict(session_id=sid, operation_status='ready' if ready else 'running', status=status,
                        next_step='assessment' if ready and status == 'reteaching' else None,
                        handoff=saved.get('tutor_handoff'), error=None)
        latest = conn.execute(
            'SELECT a.attempt_id FROM attempts a JOIN quizzes q ON q.quiz_id = a.quiz_id '
            'WHERE q.session_id = ? ORDER BY a.rowid DESC LIMIT 1', (sid,),
        ).fetchone()
        if status != 'reteaching' or retry_count != 1 or latest is None or latest[0] != attempt_id:
            raise ReteachingRejected('The reservation is stale or is no longer eligible for dispatch.')
        current = saved['current_attempt']
        attempt = conn.execute(
            'SELECT a.quiz_id, q.round, r.score FROM attempts a JOIN quizzes q ON q.quiz_id = a.quiz_id '
            'JOIN results r ON r.attempt_id = a.attempt_id WHERE a.attempt_id = ? AND a.learner_id = ?',
            (attempt_id, learner_id),
        ).fetchone()
        if not attempt or attempt[1] != 'first' or attempt[2] >= 8:
            raise ReteachingRejected('Reteaching requires the first low-scoring attempt.')
        responses = dict(conn.execute('SELECT question_id, selected_option FROM responses WHERE attempt_id = ?', (attempt_id,)))
        keys = get_answer_key(conn, attempt[0])
        if len(keys) != 10 or len(responses) != 10:
            raise ReteachingRejected('Saved grading evidence is incomplete.')
        graded = grade(keys, responses)
        expected = dict(sessionId=sid, lessonId=lesson_id, lessonVersion=version, round='first',
                        quizId=attempt[0], attemptId=attempt_id, score=graded['score'],
                        missedConceptCounts={w['concept_id']: w['missed_count'] for w in graded['weak_concepts']})
        if current != expected or graded['score'] != attempt[2]:
            raise ReteachingRejected('The reserved source attempt does not match verified grading.')
        feedback_ready = conn.execute("SELECT 1 FROM feedback_operations WHERE attempt_id = ? AND status = 'ready'", (attempt_id,)).fetchone()
        if not feedback_ready:
            raise ReteachingRejected('Validated feedback is required.')
        lesson = conn.execute('SELECT version, source_url, license, attribution FROM lessons WHERE lesson_id = ? AND version = ? AND approved = 1',
                              (lesson_id, version)).fetchone()
        if not lesson:
            raise ReteachingRejected('The approved lesson version is unavailable.')
        concepts = dict(conn.execute('SELECT concept_id, name FROM concepts WHERE lesson_id = ? AND lesson_version = ?', (lesson_id, version)))
        if conn.execute('SELECT repair_count FROM handoff_repairs WHERE handoff_id = ?', (saved['tutor_handoff_id'],)).fetchone() != (0,):
            raise ReteachingRejected('This outgoing handoff already consumed its repair budget.')
        conn.execute("UPDATE session_routes SET status = 'dispatched' WHERE attempt_id = ? AND status = 'reserved'", (attempt_id,))
    context = dict(lessonId=lesson_id, sessionId=sid, handoffId=saved['tutor_handoff_id'],
                   explanationId=saved['explanation_id'], round='retry', learningRetryCount=1,
                   returnHandoff=saved['return_handoff'], currentAttempt=current,
                   retryReservation=dict(status='dispatched', sessionId=sid, sourceAttemptId=attempt_id,
                                         handoffId=saved['tutor_handoff_id'], explanationId=saved['explanation_id']))
    repair_count = 0

    def fail(message):
        failed = dict(saved, retry_failure=message)
        with conn:
            conn.execute("UPDATE session_routes SET status = 'failed', context = ? WHERE attempt_id = ?", (json.dumps(failed), attempt_id))
            conn.execute("UPDATE study_sessions SET status = 'failed', failure_reason = ? WHERE session_id = ?", (message, sid))
            conn.execute('UPDATE handoff_repairs SET repair_count = ? WHERE handoff_id = ?', (repair_count, context['handoffId']))
        return dict(session_id=sid, operation_status='failed', status='failed', next_step=None, handoff=None, error=message)

    try:
        result = tutor(copy.deepcopy(context))
        repair_count = result['repairCount']
        if type(repair_count) is not int or repair_count not in (0, 1):
            repair_count = 0
            raise ValueError('Invalid repair count.')
        handoff = result['handoff']
        _check_handoff(handoff, context, lesson, concepts)
    except Exception as error:
        if isinstance(error, TutorFailure):
            repair_count = error.repair_count if type(error.repair_count) is int and error.repair_count in (0, 1) else 0
        return fail('Tutor could not return valid focused reteaching. The learning retry stays consumed.')
    try:
        with conn:
            conn.execute('INSERT INTO explanations VALUES (?, ?, ?, ?)',
                         (context['explanationId'], sid, 'retry', handoff['payload']['explanation_text']))
            conn.executemany('INSERT INTO explanation_concepts VALUES (?, ?)',
                             [(context['explanationId'], c['concept_id']) for c in handoff['payload']['concepts']])
            conn.execute('UPDATE handoff_repairs SET repair_count = ? WHERE handoff_id = ?', (repair_count, context['handoffId']))
            conn.execute('UPDATE session_routes SET context = ? WHERE attempt_id = ?',
                         (json.dumps(dict(saved, tutor_handoff=handoff)), attempt_id))
    except Exception:
        return fail('Could not save focused reteaching. The learning retry stays consumed.')
    return dict(session_id=sid, operation_status='ready', status='reteaching', next_step='assessment', handoff=handoff, error=None)
