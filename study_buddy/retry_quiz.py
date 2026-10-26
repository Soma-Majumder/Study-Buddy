"""Create one focused retry quiz from the controller's saved Tutor handoff."""
import json

from study_buddy.assessment import run_assessment
from study_buddy.quiz_pipeline import start_quiz_pipeline, continue_quality_quiz
from study_buddy.reviewer import run_reviewer
from study_buddy.assessment import run_assessment_revision


class RetryQuizRejected(ValueError):
    pass


def dispatch_retry_quiz(conn, learner_id, source_attempt_id, *, assessment=run_assessment, reviewer=run_reviewer, revision_assessment=run_assessment_revision):
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
            quality = conn.execute("SELECT operation_id FROM quiz_quality_operations WHERE session_id=? AND round='retry'",
                                   (sid,)).fetchone()
            if not quality:
                return dict(session_id=sid,status=status,operation_status=op_status,
                            next_step=None,
                            quiz=None,error='quality_review_required' if qid else error)
            operation_id = quality[0]
        else:
            operation_id = None
        if not existing:
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

    if operation_id:
        result = continue_quality_quiz(conn, learner_id, operation_id, assessment=assessment,
                                       reviewer=reviewer, revision_assessment=revision_assessment)
    else:
        result = start_quiz_pipeline(conn, learner_id, handoff, assessment=assessment,
                                     reviewer=reviewer, revision_assessment=revision_assessment)
    if result['status']=='failed':
        with conn:
            conn.execute("UPDATE retry_quiz_operations SET status='failed', error=? WHERE source_attempt_id=?",
                         (result['error'],source_attempt_id))
    return {key:result.get(key) for key in ('session_id','status','operation_status','next_step','quiz','error')}
