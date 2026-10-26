"""Atomically decide and reserve the learning retry, or queue a final summary."""
import json
from pathlib import Path

import jsonschema

from study_buddy.answer_keys import new_id, get_answer_key
from study_buddy.grading import grade
from study_buddy.feedback import VALIDATOR as FEEDBACK_VALIDATOR
from study_buddy.result_check import check

ROOT = Path(__file__).resolve().parent.parent
RETURN_SCHEMA = json.loads((ROOT / 'schemas/v1/assessment_to_tutor.schema.json').read_text())


class RoutingRejected(ValueError):
    pass


def decide_next_step(conn, learner_id, attempt_id):
    """Return a saved route. No agent runs and no session completes here.

    The reserved input reports the actual post-reservation retry count (1).
    The next dispatch step must verify/claim this reservation before invoking
    the Tutor; it must not pretend the retry is unused to bypass its guards.
    """
    with conn:
        # Take the SQLite write lock before reading eligibility, so two callers
        # cannot both read an unused retry and then create independent routes.
        owned = conn.execute(
            'UPDATE study_sessions SET status = status WHERE session_id IN ('
            'SELECT q.session_id FROM quizzes q JOIN attempts a ON a.quiz_id = q.quiz_id '
            'WHERE a.attempt_id = ? AND a.learner_id = ?) AND learner_id = ?',
            (attempt_id, learner_id, learner_id),
        ).rowcount
        if not owned:
            raise RoutingRejected('Attempt is unavailable for this learner.')
        row = conn.execute(
            'SELECT s.session_id, s.lesson_id, s.lesson_version, s.status, s.learning_retry_count, '
            'q.quiz_id, q.round, r.score, r.passed, r.feedback, l.source_url, l.license, l.attribution '
            'FROM attempts a JOIN quizzes q ON q.quiz_id = a.quiz_id '
            'JOIN study_sessions s ON s.session_id = q.session_id '
            'JOIN results r ON r.attempt_id = a.attempt_id '
            'JOIN lessons l ON l.lesson_id = s.lesson_id AND l.version = s.lesson_version '
            'WHERE a.attempt_id = ? AND s.learner_id = ?', (attempt_id, learner_id),
        ).fetchone()
        if row is None:
            raise RoutingRejected('A saved graded result is required.')
        sid, lesson, version, status, retry_count, qid, round_, score, passed, feedback_json, url, license_, credit = row
        latest = conn.execute(
            'SELECT a.attempt_id FROM attempts a JOIN quizzes q ON q.quiz_id = a.quiz_id '
            'WHERE q.session_id = ? ORDER BY a.rowid DESC LIMIT 1', (sid,),
        ).fetchone()
        if not latest or latest[0] != attempt_id:
            raise RoutingRejected('Only the current attempt can determine the next step.')
        existing = conn.execute('SELECT context, status FROM session_routes WHERE attempt_id = ?', (attempt_id,)).fetchone()
        if existing:
            saved = json.loads(existing[0])
            if status != saved['status']:
                raise RoutingRejected('The session has moved beyond this reserved route.')
            return dict(operation_status=existing[1], **saved)
        if status != 'grading':
            raise RoutingRejected('Session is not awaiting a routing decision.')
        op = conn.execute("SELECT handoff_id FROM feedback_operations WHERE attempt_id = ? AND status = 'ready'",
                          (attempt_id,)).fetchone()
        if not op:
            raise RoutingRejected('Validated, saved Assessment feedback is required.')
        try:
            feedback = json.loads(feedback_json)
            FEEDBACK_VALIDATOR.validate(feedback)
            responses = dict(conn.execute('SELECT question_id, selected_option FROM responses WHERE attempt_id = ?',
                                          (attempt_id,)))
            keys = get_answer_key(conn, qid)
            if len(keys) != 10 or len(responses) != 10:
                raise ValueError('Incomplete grading.')
            graded = grade(keys, responses)
            weak = dict(conn.execute('SELECT concept_id, missed_count FROM weak_concepts WHERE source_attempt_id = ? AND learner_id = ?',
                                     (attempt_id, learner_id)))
            if (graded['score'] != score or graded['passed'] != bool(passed) or
                    weak != {w['concept_id']: w['missed_count'] for w in graded['weak_concepts']} or
                    feedback['quiz_id'] != qid or feedback['attempt_id'] != attempt_id or
                    len({w['concept_id'] for w in feedback['weak_concepts']}) != len(feedback['weak_concepts']) or
                    check(feedback, graded)):
                raise ValueError('Inconsistent evidence.')
        except Exception:
            raise RoutingRejected('Saved grading and feedback must agree before routing.') from None

        if round_ == 'first' and score < 8 and retry_count == 0:
            outgoing_id, explanation_id = new_id('handoff'), new_id('expl')
            handoff = dict(schema_version='1.0', handoff_id=op[0], direction='assessment_to_tutor',
                           task='reteach_weak_concepts', context=dict(learner_session_id=sid, round='first'),
                           sources=[dict(lesson_id=lesson, lesson_version=version, url=url, license=license_, attribution=credit)],
                           expected_output='A short explanation of only the verified weak concepts, grounded in the approved lesson.',
                           uncertainty=dict(level='low', reasons=['Missed counts are controller-verified; feedback notes are not a confirmed diagnosis.']),
                           payload=dict(lesson_id=lesson, lesson_version=version, quiz_id=qid,
                                        attempt_id=attempt_id, score=score, weak_concepts=feedback['weak_concepts']))
            jsonschema.Draft202012Validator(RETURN_SCHEMA).validate(handoff)
            changed = conn.execute(
                "UPDATE study_sessions SET learning_retry_count = 1, status = 'reteaching' "
                "WHERE session_id = ? AND learning_retry_count = 0 AND status = 'grading'", (sid,),
            ).rowcount
            if changed != 1:
                raise RoutingRejected('The learning retry could not be reserved.')
            conn.execute("INSERT INTO handoff_repairs VALUES (?, ?, 'tutor_to_assessment', 0)", (outgoing_id, sid))
            route, operation_status = 'reteach', 'reserved'
            context = dict(session_id=sid, attempt_id=attempt_id, status='reteaching', next_step='reteach',
                           learning_retry_count=1, return_handoff=handoff,
                           tutor_handoff_id=outgoing_id, explanation_id=explanation_id,
                           current_attempt=dict(sessionId=sid, lessonId=lesson, lessonVersion=version,
                                                round='first', quizId=qid, attemptId=attempt_id,
                                                score=score, missedConceptCounts=weak))
        else:
            route, operation_status = 'summary', 'summary_pending'
            conn.execute("UPDATE study_sessions SET status = 'pending_save' WHERE session_id = ?", (sid,))
            reason = 'retry_finished' if round_ == 'retry' else ('passed' if score >= 8 else 'retry_unavailable')
            context = dict(session_id=sid, attempt_id=attempt_id, status='pending_save', next_step='summary',
                           learning_retry_count=retry_count, reason=reason)
        conn.execute('INSERT INTO session_routes VALUES (?, ?, ?, ?, ?)',
                     (attempt_id, sid, route, json.dumps(context), operation_status))
    return dict(operation_status=operation_status, **context)
