"""Create one focused retry quiz from the controller's saved Tutor handoff."""
import copy
import json

from scripts.validate_quiz import check
from study_buddy.assessment import run_assessment
from study_buddy.answer_keys import new_id, save_quiz, get_client_quiz


class RetryQuizRejected(ValueError):
    pass


def dispatch_retry_quiz(conn, learner_id, source_attempt_id, *, assessment=run_assessment):
    from study_buddy.controller import _check_handoff
    with conn:
        owned = conn.execute(
            'UPDATE study_sessions SET status = status WHERE learner_id = ? AND session_id IN ('
            'SELECT session_id FROM session_routes WHERE attempt_id = ?)', (learner_id, source_attempt_id),
        ).rowcount
        if not owned:
            raise RetryQuizRejected('Retry session is unavailable for this learner.')
        row = conn.execute(
            'SELECT sr.session_id, sr.context, sr.status, s.status, s.learning_retry_count, '
            's.lesson_id, s.lesson_version FROM session_routes sr '
            'JOIN study_sessions s ON s.session_id = sr.session_id '
            "WHERE sr.attempt_id = ? AND sr.route = 'reteach' AND s.learner_id = ?",
            (source_attempt_id, learner_id),
        ).fetchone()
        if not row:
            raise RetryQuizRejected('A saved retry route is required.')
        sid, context_json, route_status, status, retry_count, lesson_id, version = row
        existing = conn.execute('SELECT quiz_id, status, error FROM retry_quiz_operations WHERE source_attempt_id = ?',
                                (source_attempt_id,)).fetchone()
        if existing:
            qid, op_status, error = existing
            unsubmitted = not conn.execute('SELECT 1 FROM attempts WHERE quiz_id = ?', (qid,)).fetchone()
            return dict(session_id=sid, status=status, operation_status=op_status,
                        next_step='submit_answers' if op_status == 'ready' and status == 'awaiting_answers' and unsubmitted else None,
                        quiz=get_client_quiz(conn, qid) if op_status == 'ready' else None, error=error)
        saved = json.loads(context_json)
        latest = conn.execute(
            'SELECT a.attempt_id FROM attempts a JOIN quizzes q ON q.quiz_id = a.quiz_id '
            'WHERE q.session_id = ? ORDER BY a.rowid DESC LIMIT 1', (sid,),
        ).fetchone()
        if (status != 'reteaching' or retry_count != 1 or route_status != 'dispatched' or
                'tutor_handoff' not in saved or not latest or latest[0] != source_attempt_id):
            raise RetryQuizRejected('A saved focused explanation for the active retry is required.')
        handoff = saved['tutor_handoff']
        context = dict(lessonId=lesson_id, sessionId=sid, handoffId=saved['tutor_handoff_id'],
                       explanationId=saved['explanation_id'], round='retry', returnHandoff=saved['return_handoff'])
        lesson = conn.execute('SELECT version, source_url, license, attribution FROM lessons WHERE lesson_id = ? AND version = ? AND approved = 1',
                              (lesson_id, version)).fetchone()
        concepts = dict(conn.execute('SELECT concept_id, name FROM concepts WHERE lesson_id = ? AND lesson_version = ?',
                                     (lesson_id, version)))
        if not lesson:
            raise RetryQuizRejected('Approved lesson version is unavailable.')
        _check_handoff(handoff, context, lesson, concepts)
        stored = conn.execute("SELECT text FROM explanations WHERE explanation_id = ? AND session_id = ? AND round = 'retry'",
                              (context['explanationId'], sid)).fetchone()
        stored_concepts = {r[0] for r in conn.execute('SELECT concept_id FROM explanation_concepts WHERE explanation_id = ?',
                                                   (context['explanationId'],))}
        if not stored or stored[0] != handoff['payload']['explanation_text'] or stored_concepts != {c['concept_id'] for c in handoff['payload']['concepts']}:
            raise RetryQuizRejected('The handoff must match the saved focused explanation.')
        conn.execute("INSERT INTO retry_quiz_operations VALUES (?, ?, ?, NULL, 'running', NULL)",
                     (source_attempt_id, sid, context['handoffId']))

    def fail(message):
        with conn:
            conn.execute("UPDATE retry_quiz_operations SET status = 'failed', error = ? WHERE source_attempt_id = ?",
                         (message, source_attempt_id))
            conn.execute("UPDATE study_sessions SET status = 'failed', failure_reason = ? WHERE session_id = ?", (message, sid))
        return dict(session_id=sid, status='failed', operation_status='failed', next_step=None, quiz=None, error=message)

    repair_request = None
    while True:
        try:
            output = assessment(copy.deepcopy(handoff), repair_request=repair_request)
        except Exception:
            return fail('Assessment could not create the focused retry quiz. The learning retry stays consumed.')
        try:
            valid = not check(output, handoff)
        except Exception:
            valid = False
        if valid:
            break
        with conn:
            remaining = conn.execute(
                'UPDATE handoff_repairs SET repair_count = 1 WHERE handoff_id = ? AND repair_count = 0',
                (context['handoffId'],),
            ).rowcount
        if not remaining:
            return fail('The focused retry quiz stayed invalid after the handoff repair budget was used.')
        repair_request = ('Return exactly 10 focused questions matching the original retry handoff and explanation IDs. '
                          'Use round retry, only the taught focus concepts, four distinct options per question, '
                          'and one private key entry per question. This is the only remaining handoff repair.')
    qid = new_id('quiz')
    try:
        with conn:
            save_quiz(conn, qid, sid, context['explanationId'], output, manage_transaction=False)
            client = get_client_quiz(conn, qid)
            conn.execute("UPDATE retry_quiz_operations SET status = 'ready', quiz_id = ? WHERE source_attempt_id = ?", (qid, source_attempt_id))
            conn.execute("UPDATE study_sessions SET status = 'awaiting_answers' WHERE session_id = ?", (sid,))
    except Exception:
        return fail('Could not save the focused retry quiz. The learning retry stays consumed.')
    return dict(session_id=sid, status='awaiting_answers', operation_status='ready', next_step='submit_answers', quiz=client, error=None)
