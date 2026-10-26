"""Controller-owned Assessment feedback for a saved, graded attempt.

The callback makes one generation per invocation. This module owns validation,
its one repair, and saved operation state. It does not dispatch reteaching.
"""
import copy
import json
from pathlib import Path
import sqlite3

import jsonschema

from study_buddy.answer_keys import new_id, get_answer_key
from study_buddy.grading import grade
from study_buddy.result_check import check

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / 'schemas/v1/result_explanation.schema.json').read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


class FeedbackRejected(ValueError):
    pass


def explain_results(conn, learner_id, attempt_id, *, assessment):
    row = conn.execute(
        'SELECT s.session_id, s.status, s.lesson_id, s.lesson_version, q.quiz_id, q.round, '
        'r.result_id, r.score, r.passed, e.text, l.source_url, l.license, l.attribution '
        'FROM attempts a JOIN quizzes q ON q.quiz_id = a.quiz_id '
        'JOIN study_sessions s ON s.session_id = q.session_id '
        'JOIN results r ON r.attempt_id = a.attempt_id '
        'JOIN explanations e ON e.explanation_id = q.explanation_id '
        'JOIN lessons l ON l.lesson_id = s.lesson_id AND l.version = s.lesson_version '
        'WHERE a.attempt_id = ? AND a.learner_id = ? AND s.learner_id = ?',
        (attempt_id, learner_id, learner_id),
    ).fetchone()
    if row is None:
        raise FeedbackRejected('Graded attempt is unavailable for this learner.')
    (session_id, status, lesson_id, version, quiz_id, round_, result_id, score, passed,
     text, url, license_, attribution) = row

    def existing():
        op = conn.execute('SELECT handoff_id, status FROM feedback_operations WHERE attempt_id = ?',
                          (attempt_id,)).fetchone()
        if op is None:
            return None
        feedback = None
        if op[1] == 'ready':
            feedback = json.loads(conn.execute('SELECT feedback FROM results WHERE result_id = ?',
                                               (result_id,)).fetchone()[0])
        current_status = conn.execute('SELECT status FROM study_sessions WHERE session_id = ?',
                                      (session_id,)).fetchone()[0]
        return dict(session_id=session_id, attempt_id=attempt_id, operation_status=op[1],
                    status=current_status, score=score, passed=bool(passed), next_step='retry_decision' if op[1] == 'ready' and current_status == 'grading' else None,
                    feedback=feedback, error='Assessment feedback failed.' if op[1] == 'failed' else None)

    previous = existing()
    if previous:
        return previous
    latest = conn.execute(
        'SELECT a.attempt_id FROM attempts a JOIN quizzes q ON q.quiz_id = a.quiz_id '
        'WHERE q.session_id = ? ORDER BY a.rowid DESC LIMIT 1', (session_id,),
    ).fetchone()
    if status != 'grading' or latest is None or latest[0] != attempt_id:
        raise FeedbackRejected('This attempt is not awaiting feedback.')
    keys = get_answer_key(conn, quiz_id)
    responses = dict(conn.execute('SELECT question_id, selected_option FROM responses WHERE attempt_id = ?',
                                  (attempt_id,)))
    if len(keys) != 10 or len(responses) != 10:
        raise FeedbackRejected('Saved grading evidence is incomplete.')
    graded = grade(keys, responses)
    if graded['score'] != score or graded['passed'] != bool(passed):
        raise FeedbackRejected('Saved result does not match verified grading evidence.')
    weak = dict(conn.execute('SELECT concept_id, missed_count FROM weak_concepts WHERE source_attempt_id = ? AND learner_id = ?',
                            (attempt_id, learner_id)))
    if weak != {w['concept_id']: w['missed_count'] for w in graded['weak_concepts']}:
        raise FeedbackRejected('Saved weak concepts do not match verified grading evidence.')
    context = dict(task='explain_verified_results', session_id=session_id,
                   lesson_id=lesson_id, lesson_version=version, quiz_id=quiz_id, attempt_id=attempt_id,
                   round=round_, explanation_text=text, graded=graded, output_schema=SCHEMA,
                   sources=[dict(lesson_id=lesson_id, lesson_version=version, url=url,
                                 license=license_, attribution=attribution)])
    handoff_id = new_id('handoff')
    try:
        with conn:
            # This local transaction claims the attempt before any external call.
            current = conn.execute('SELECT status FROM study_sessions WHERE session_id = ?', (session_id,)).fetchone()[0]
            if current != 'grading':
                raise FeedbackRejected('Session is no longer awaiting feedback.')
            conn.execute("INSERT INTO handoff_repairs VALUES (?, ?, 'assessment_to_tutor', 0)",
                         (handoff_id, session_id))
            conn.execute("INSERT INTO feedback_operations VALUES (?, ?, 'running')", (attempt_id, handoff_id))
    except sqlite3.IntegrityError:
        previous = existing()
        if previous:
            return previous
        raise
    context['handoff_id'] = handoff_id

    def fail(message):
        with conn:
            conn.execute("UPDATE feedback_operations SET status = 'failed' WHERE attempt_id = ?", (attempt_id,))
            conn.execute("UPDATE study_sessions SET status = 'failed', failure_reason = ? WHERE session_id = ?",
                         (message, session_id))
        return dict(session_id=session_id, attempt_id=attempt_id, operation_status='failed',
                    status='failed', score=score, passed=bool(passed), next_step=None, feedback=None, error=message)

    repair_request = None
    while True:
        try:
            output = assessment(copy.deepcopy(context), repair_request=repair_request)
        except Exception:
            return fail('Assessment could not explain the saved result. The verified score is preserved.')
        try:
            VALIDATOR.validate(output)
            valid = (output['quiz_id'] == quiz_id and output['attempt_id'] == attempt_id and
                     output['summary'].strip() and all(f['explanation'].strip() for f in output['feedback']) and
                     len({w['concept_id'] for w in output['weak_concepts']}) == len(output['weak_concepts']) and
                     not check(output, graded))
        except Exception:
            valid = False
        if valid:
            break
        with conn:
            reserved = conn.execute(
                'UPDATE handoff_repairs SET repair_count = 1 WHERE handoff_id = ? AND repair_count = 0',
                (handoff_id,),
            ).rowcount
        if not reserved:
            return fail('Assessment feedback stayed invalid after one repair. The verified score is preserved.')
        repair_request = ('Return a complete result-explanation object for the original quiz and attempt. '
                          'Provide ten nonempty feedback entries, one per question position, and exactly the '
                          'verified weak concepts and missed counts. Do not add a score field or extra fields. '
                          'This is the single repair; explain the saved grading without changing it.')
    try:
        with conn:
            conn.execute('UPDATE results SET feedback = ? WHERE result_id = ?', (json.dumps(output), result_id))
            conn.execute("UPDATE feedback_operations SET status = 'ready' WHERE attempt_id = ?", (attempt_id,))
    except sqlite3.Error:
        return fail('Could not save Assessment feedback. The verified score is preserved.')
    return existing()
