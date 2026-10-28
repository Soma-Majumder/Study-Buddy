"""Controller-owned Assessment feedback for a saved, graded attempt.

The callback makes one generation per invocation. This module owns validation,
its one repair, and saved operation state. It does not dispatch reteaching.
"""
import copy
import json
import re
from pathlib import Path
import sqlite3

import jsonschema

from study_buddy.answer_keys import new_id, get_answer_key
from study_buddy.feedback_runner import run_feedback
from study_buddy.grading import grade
from study_buddy.operation_deadlines import record_operation_deadline, check_operation_deadline_locked
from study_buddy.result_check import check

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / 'schemas/v1/result_explanation.schema.json').read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


class FeedbackRejected(ValueError):
    pass


def _cholesterol_qualifier_issues(output, graded, approved_passage):
    """Narrow regression guard, not a general factual-accuracy classifier."""
    if 'helps prevent' not in approved_passage.lower():
        return []
    positions = {item['position'] for item in graded['items']
                 if item['concept_id'] == 'c_cholesterol_buffer'}
    issues = []
    for item in output['feedback']:
        text = ' '.join(item['explanation'].lower().split())
        if item['position'] in positions and re.search(
                r'\bprevents? (?:cold-induced )?stiffening\b', text):
            # "helps prevent" retains the qualifier; an additional unqualified
            # occurrence must still be rejected.
            remainder = re.sub(r'\bhelps? prevent (?:cold-induced )?stiffening\b', '', text)
            if re.search(r'\bprevents? (?:cold-induced )?stiffening\b', remainder):
                issues.append(item['position'])
    return issues


def _answer_summary(graded):
    """Describe saved selections only; no reasoning trace was collected."""
    total = len(graded['items'])
    summary = f"You answered {graded['score']} of {total} questions correctly in this attempt."
    missed = [str(item['position']) for item in graded['items'] if not item['is_correct']]
    if missed:
        return summary + ' Review the feedback for questions ' + ', '.join(missed) + ' and revisit those lesson concepts.'
    return summary + ' Revisit the lesson to reinforce these ideas.'


def explain_results(conn, learner_id, attempt_id, *, assessment=run_feedback):
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
        with conn:
            conn.execute('UPDATE study_sessions SET status=status WHERE session_id=? AND learner_id=?', (session_id, learner_id))
            op = conn.execute('SELECT handoff_id,status FROM feedback_operations WHERE attempt_id=?', (attempt_id,)).fetchone()
            if op is None:
                return None
            check_operation_deadline_locked(conn, 'feedback', learner_id, attempt_id)
            op = conn.execute('SELECT handoff_id,status FROM feedback_operations WHERE attempt_id=?', (attempt_id,)).fetchone()
            feedback = None
            if op[1] == 'ready':
                feedback = json.loads(conn.execute('SELECT feedback FROM results WHERE result_id=?', (result_id,)).fetchone()[0])
            current_status = conn.execute('SELECT status FROM study_sessions WHERE session_id=?', (session_id,)).fetchone()[0]
            return dict(session_id=session_id, attempt_id=attempt_id, operation_status=op[1],
                        status=current_status, score=score, passed=bool(passed),
                        next_step='retry_decision' if op[1] == 'ready' and current_status == 'grading' else None,
                        feedback=feedback, error='Assessment feedback failed.' if op[1] == 'failed' else None)

    def active_locked():
        conn.execute('UPDATE study_sessions SET status=status WHERE session_id=? AND learner_id=?', (session_id, learner_id))
        return check_operation_deadline_locked(conn, 'feedback', learner_id, attempt_id)

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
            record_operation_deadline(conn, 'feedback', attempt_id)
    except sqlite3.IntegrityError:
        previous = existing()
        if previous:
            return previous
        raise
    context['handoff_id'] = handoff_id

    def fail(message):
        with conn:
            allowed = active_locked()
            if allowed:
                conn.execute("UPDATE feedback_operations SET status='failed' WHERE attempt_id=? AND status='running'", (attempt_id,))
                conn.execute("UPDATE study_sessions SET status='failed',failure_reason=? WHERE session_id=? AND status='grading'", (message, session_id))
        if not allowed:
            return existing()
        return dict(session_id=session_id, attempt_id=attempt_id, operation_status='failed',
                    status='failed', score=score, passed=bool(passed), next_step=None, feedback=None, error=message)

    evidence = conn.execute(
        'SELECT c.source_passage FROM concepts c JOIN lessons l '
        'ON l.lesson_id=c.lesson_id AND l.version=c.lesson_version '
        'WHERE c.concept_id=? AND c.lesson_id=? AND c.lesson_version=? AND l.approved=1',
        ('c_cholesterol_buffer', lesson_id, version),
    ).fetchone()
    cholesterol_passage = evidence[0] if evidence else ''
    repair_request = None
    while True:
        with conn:
            allowed = active_locked()
        if not allowed:
            return existing()
        try:
            output = assessment(copy.deepcopy(context), repair_request=repair_request)
        except Exception:
            return fail('Assessment could not explain the saved result. The verified score is preserved.')
        qualifier_issues = []
        try:
            VALIDATOR.validate(output)
            valid = (output['quiz_id'] == quiz_id and output['attempt_id'] == attempt_id and
                     output['summary'].strip() and all(f['explanation'].strip() for f in output['feedback']) and
                     len({w['concept_id'] for w in output['weak_concepts']}) == len(output['weak_concepts']) and
                     not check(output, graded))
            if valid:
                qualifier_issues = _cholesterol_qualifier_issues(output, graded, cholesterol_passage)
                valid = not qualifier_issues
        except Exception:
            valid = False
        if valid:
            break
        with conn:
            allowed = active_locked()
            reserved = conn.execute(
                'UPDATE handoff_repairs SET repair_count = 1 WHERE handoff_id = ? AND repair_count = 0',
                (handoff_id,),
            ).rowcount if allowed else 0
        if not allowed:
            return existing()
        if not reserved:
            return fail('Assessment feedback stayed invalid after one repair. The verified score is preserved.')
        repair_request = ('Return a complete result-explanation object for the original quiz and attempt. '
                          'Provide ten nonempty feedback entries, one per question position, and exactly the '
                          'verified weak concepts and missed counts. Do not add a score field or extra fields. '
                          'This is the single repair; explain the saved grading without changing it.')
        if qualifier_issues:
            repair_request += (' For cholesterol feedback at positions ' +
                               ', '.join(map(str, qualifier_issues)) +
                               ', preserve the approved qualifier: cholesterol helps prevent '
                               'cold-induced stiffening; do not say it prevents stiffening outright.')
    # Optional routing notes quote approved evidence instead of model paraphrases.
    # Work on a copy: preserve the provider's original output for callers/logging.
    output = copy.deepcopy(output)
    # The controller observes selected answers, not the student's reasoning.
    output['summary'] = _answer_summary(graded)
    for concept in output['weak_concepts']:
        if 'note' not in concept:
            continue
        evidence = conn.execute(
            'SELECT c.source_passage FROM concepts c JOIN lessons l '
            'ON l.lesson_id=c.lesson_id AND l.version=c.lesson_version '
            'WHERE c.concept_id=? AND c.lesson_id=? AND c.lesson_version=? AND l.approved=1',
            (concept['concept_id'], lesson_id, version),
        ).fetchone()
        if evidence and evidence[0].strip():
            concept['note'] = 'Review this approved lesson passage: ' + evidence[0].strip()
        else:
            # The note is optional. Do not invent a replacement without evidence.
            concept.pop('note')
    try:
        with conn:
            allowed = active_locked()
            if allowed:
                conn.execute('UPDATE results SET feedback = ? WHERE result_id = ?', (json.dumps(output), result_id))
                conn.execute("UPDATE feedback_operations SET status = 'ready' WHERE attempt_id = ?", (attempt_id,))
    except sqlite3.Error:
        return fail('Could not save Assessment feedback. The verified score is preserved.')
    return existing()
