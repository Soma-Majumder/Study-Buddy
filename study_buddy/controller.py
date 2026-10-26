"""First routing step: approved lesson -> Tutor -> stored explanation or failure.

learner_id must come from trusted application context, not an agent. This does
not authenticate users. start_quiz_session dispatches an injected Assessment runner. Use a connection with schema.sql
and approved lessons already loaded by study_buddy.lessons.load_lessons.
"""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sqlite3

import jsonschema

from study_buddy.answer_keys import new_id, save_quiz, get_client_quiz, get_answer_key
from scripts.validate_quiz import check as check_quiz
from study_buddy.grading import grade
from study_buddy.feedback import explain_results
from study_buddy.routing import decide_next_step
from study_buddy.reteaching import dispatch_reteaching

ROOT = Path(__file__).resolve().parent.parent
HANDOFF_SCHEMA = json.loads((ROOT / 'schemas/v1/tutor_to_assessment.schema.json').read_text())


class TutorFailure(Exception):
    def __init__(self, stage='model call', repair_count=0):
        super().__init__('Tutor could not produce a valid explanation.')
        self.stage = stage
        self.repair_count = repair_count


def run_tutor(context):
    """Invoke the existing JS harness. Credentials stay in the environment."""
    node = os.environ.get('STUDY_BUDDY_NODE', 'node')
    try:
        process = subprocess.run(
            [node, str(ROOT / 'backend/tutor-command.mjs')],
            input=json.dumps(context), text=True, capture_output=True,
            cwd=ROOT, timeout=75,
        )
        body = json.loads(process.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise TutorFailure('provider adapter') from None
    if process.returncode != 0 or not body.get('ok'):
        error = body.get('error', {})
        count = error.get('repairCount', 0)
        raise TutorFailure(error.get('stage', 'provider adapter'), count if count in (0, 1) else 0)
    return body['result']


def _check_handoff(handoff, context, lesson, concepts):
    jsonschema.Draft202012Validator(HANDOFF_SCHEMA).validate(handoff)
    p = handoff['payload']
    if (handoff['handoff_id'] != context['handoffId'] or
            handoff['context']['learner_session_id'] != context['sessionId'] or
            handoff['context']['round'] != context['round'] or
            p['explanation_id'] != context['explanationId'] or
            p['lesson_id'] != context['lessonId'] or p['lesson_version'] != lesson[0]):
        raise ValueError('Handoff does not belong to the current session and lesson.')
    if handoff['sources'] != [{
            'lesson_id': context['lessonId'], 'lesson_version': lesson[0],
            'url': lesson[1], 'license': lesson[2], 'attribution': lesson[3]}]:
        raise ValueError('Handoff sources do not match the approved lesson.')
    seen = set()
    for c in p['concepts']:
        if (c['concept_id'] not in concepts or c['concept_id'] in seen or
                c['name'] != concepts[c['concept_id']] or not c['taught_summary'].strip()):
            raise ValueError('Invalid taught concepts.')
        seen.add(c['concept_id'])
    if not p['explanation_text'].strip():
        raise ValueError('Empty explanation.')
    if context['round'] == 'first' and 'focus_concept_ids' in p:
        raise ValueError('Invalid first-round explanation.')
    if context['round'] == 'retry':
        focus = {w['concept_id'] for w in context['returnHandoff']['payload']['weak_concepts']}
        returned_focus = p.get('focus_concept_ids', [])
        if (len(returned_focus) != len(focus) or set(returned_focus) != focus or seen != focus):
            raise ValueError('Retry explanation must teach exactly the verified focus concepts.')


def start_session(conn, learner_id, lesson_id, *, tutor=run_tutor):
    """Return {session_id, status, next_step, handoff, error}.

    The real Tutor harness owns generation validation and its one repair.
    Controller validation is a final boundary check; it never restarts the
    harness or grants another repair. Repair outcomes are saved on return;
    crash-safe repair reservation during the JS call remains future work.
    """
    if not isinstance(learner_id, str) or not re.fullmatch(r'learner_[a-z0-9]{12}', learner_id):
        raise ValueError('A controller-owned learner ID is required.')
    if not conn.execute('SELECT 1 FROM learners WHERE learner_id = ?', (learner_id,)).fetchone():
        raise ValueError('Learner is not registered.')
    lesson = conn.execute(
        'SELECT version, source_url, license, attribution FROM lessons '
        'WHERE lesson_id = ? AND approved = 1 ORDER BY version DESC LIMIT 1', (lesson_id,),
    ).fetchone()
    if not lesson:
        raise ValueError('An approved lesson is required.')
    concepts = dict(conn.execute(
        'SELECT concept_id, name FROM concepts WHERE lesson_id = ? AND lesson_version = ?',
        (lesson_id, lesson[0]),
    ))
    context = dict(lessonId=lesson_id, sessionId=new_id('session'),
                   handoffId=new_id('handoff'), explanationId=new_id('expl'), round='first')
    session_id = context['sessionId']
    with conn:
        conn.execute(
            "INSERT INTO study_sessions (session_id, learner_id, lesson_id, lesson_version, status, created_at) "
            "VALUES (?, ?, ?, ?, 'teaching', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
            (session_id, learner_id, lesson_id, lesson[0]),
        )
        conn.execute(
            "INSERT INTO handoff_repairs VALUES (?, ?, 'tutor_to_assessment', 0)",
            (context['handoffId'], session_id),
        )
    repair_count = 0
    try:
        result = tutor(context)
        repair_count = result['repairCount']
        if type(repair_count) is not int or repair_count not in (0, 1):
            raise ValueError('Invalid repair count.')
        handoff = result['handoff']
        _check_handoff(handoff, context, lesson, concepts)
    except Exception as error:
        count = error.repair_count if isinstance(error, TutorFailure) else repair_count
        return _fail(conn, context, count if type(count) is int and count in (0, 1) else 0,
                     'Tutor did not return a valid explanation. No quiz was started.')

    try:
        with conn:
            conn.execute('INSERT INTO explanations VALUES (?, ?, ?, ?)',
                         (context['explanationId'], session_id, 'first', handoff['payload']['explanation_text']))
            conn.executemany('INSERT INTO explanation_concepts VALUES (?, ?)',
                             [(context['explanationId'], c['concept_id']) for c in handoff['payload']['concepts']])
            conn.execute('UPDATE handoff_repairs SET repair_count = ? WHERE handoff_id = ?',
                         (repair_count, context['handoffId']))
    except Exception:
        return _fail(conn, context, repair_count, 'Could not save the explanation. No quiz was started.')
    # A quiz does not exist yet: keep teaching status, never awaiting_answers or completed.
    return dict(session_id=session_id, status='teaching', next_step='assessment', handoff=handoff, error=None)


def _fail(conn, context, repair_count, message):
    # If recording failure itself fails, propagate the DB error instead of claiming a save.
    with conn:
        conn.execute("UPDATE study_sessions SET status = 'failed', failure_reason = ? WHERE session_id = ?",
                     (message, context['sessionId']))
        conn.execute('UPDATE handoff_repairs SET repair_count = ? WHERE handoff_id = ?',
                     (repair_count, context['handoffId']))
    return dict(session_id=context['sessionId'], status='failed', next_step=None, handoff=None, error=message)

def start_quiz_session(conn, learner_id, lesson_id, *, assessment, tutor=run_tutor):
    """Route a validated Tutor handoff to an injected Assessment runner.

    assessment(handoff, repair_request=None) returns a private quiz-output
    object. There is no production AI Assessment runner yet. The controller
    owns validation and the remaining repair budget for this handoff.
    """
    started = start_session(conn, learner_id, lesson_id, tutor=tutor)
    if started['status'] == 'failed':
        return started
    handoff = started['handoff']
    context = dict(sessionId=started['session_id'], handoffId=handoff['handoff_id'])
    repair_request = None
    while True:
        count = conn.execute('SELECT repair_count FROM handoff_repairs WHERE handoff_id = ?',
                             (context['handoffId'],)).fetchone()[0]
        try:
            # A model adapter must not mutate the controller's trusted copy.
            output = assessment(copy.deepcopy(handoff), repair_request=repair_request)
        except Exception:
            return _fail(conn, context, count, 'Assessment could not create a quiz. No quiz was started.')
        try:
            valid = not check_quiz(output, handoff)
        except Exception:
            valid = False
        if valid:
            break
        # Reserve before correction. Tutor and Assessment do not get fresh
        # budgets for the same outgoing handoff. No unbounded repair loop.
        with conn:
            reserved = conn.execute(
                'UPDATE handoff_repairs SET repair_count = 1 WHERE handoff_id = ? AND repair_count = 0',
                (context['handoffId'],),
            ).rowcount
        if not reserved:
            return _fail(conn, context, 1, 'Assessment returned an invalid quiz after the repair budget was used. No quiz was started.')
        repair_request = ('Return a complete quiz matching the original handoff and explanation IDs and round. '
                          'Use exactly 10 questions on taught concepts, four distinct options per question, '
                          'and one private answer-key entry per question. This is the only remaining repair; '
                          'do not include extra fields.')

    quiz_id = new_id('quiz')
    try:
        with conn:
            # One transaction includes questions, keys, and the ready status.
            save_quiz(conn, quiz_id, context['sessionId'], handoff['payload']['explanation_id'],
                      output, manage_transaction=False)
            client_quiz = get_client_quiz(conn, quiz_id)
            conn.execute("UPDATE study_sessions SET status = 'awaiting_answers' WHERE session_id = ?",
                         (context['sessionId'],))
    except Exception:
        return _fail(conn, context, conn.execute(
            'SELECT repair_count FROM handoff_repairs WHERE handoff_id = ?',
            (context['handoffId'],)).fetchone()[0], 'Could not save the quiz. No quiz was started.')
    return dict(session_id=context['sessionId'], status='awaiting_answers', next_step='submit_answers',
                quiz=client_quiz, error=None)

class SubmissionRejected(ValueError):
    """Invalid, unauthorized, stale, or already-submitted learner input."""


def submit_answers(conn, learner_id, quiz_id, answers):
    """Grade one complete submission and atomically save verified evidence.

    Rejected input creates no attempt or score. A claimed submission writes
    its attempt, responses, result, and weak concepts in one transaction.
    Session status stays grading for later Assessment feedback/routing.
    """
    row = conn.execute(
        'SELECT s.session_id, s.lesson_id, s.status FROM study_sessions s '
        'JOIN quizzes q ON q.session_id = s.session_id '
        'WHERE q.quiz_id = ? AND s.learner_id = ?', (quiz_id, learner_id),
    ).fetchone()
    if row is None:
        raise SubmissionRejected('Quiz is unavailable for this learner.')
    session_id, lesson_id, status = row
    if status != 'awaiting_answers':
        raise SubmissionRejected('This session is not accepting answers.')
    questions = {r[0] for r in conn.execute('SELECT question_id FROM questions WHERE quiz_id = ?', (quiz_id,))}
    if (not isinstance(answers, dict) or set(answers) != questions or
            any(not isinstance(v, str) or v not in ('a', 'b', 'c', 'd') for v in answers.values())):
        raise SubmissionRejected('Answer every quiz question with one option from a to d.')
    keys = get_answer_key(conn, quiz_id)
    if len(questions) != 10 or len(keys) != 10 or {k['question_id'] for k in keys} != questions:
        # A technical defect never becomes a learner's wrong answer.
        with conn:
            conn.execute("UPDATE study_sessions SET status = 'failed', failure_reason = ? WHERE session_id = ?",
                         ('The saved quiz or answer key is incomplete. No score was recorded.', session_id))
        return dict(session_id=session_id, status='failed', next_step=None, graded=None,
                    error='The saved quiz or answer key is incomplete. No score was recorded.')
    graded = grade(keys, answers)
    attempt_id, result_id = new_id('attempt'), new_id('result')
    recorded_at = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
    with conn:
        # Conditional update acquires SQLite's write lock. A simultaneous or
        # repeated submission cannot claim a session already moved to grading.
        claimed = conn.execute(
            "UPDATE study_sessions SET status = 'grading' WHERE session_id = ? AND status = 'awaiting_answers'",
            (session_id,),
        ).rowcount
        if not claimed or conn.execute('SELECT 1 FROM attempts WHERE quiz_id = ?', (quiz_id,)).fetchone():
            raise SubmissionRejected('This quiz was already submitted or is no longer accepting answers.')
        conn.execute('SAVEPOINT save_submission')
        try:
            conn.execute('INSERT INTO attempts VALUES (?, ?, ?, ?)',
                         (attempt_id, quiz_id, learner_id, recorded_at))
            conn.executemany('INSERT INTO responses VALUES (?, ?, ?, ?)', [
                (attempt_id, i['question_id'], i['selected'], int(i['is_correct'])) for i in graded['items']
            ])
            # These are deterministic key-based rationales, not AI-written feedback.
            conn.execute('INSERT INTO results VALUES (?, ?, ?, ?, ?, ?)',
                         (result_id, attempt_id, graded['score'], json.dumps(graded['items']),
                          int(graded['passed']), recorded_at))
            conn.executemany('INSERT INTO weak_concepts VALUES (?, ?, ?, ?, ?, ?)', [
                (learner_id, w['concept_id'], attempt_id, lesson_id, w['missed_count'], recorded_at)
                for w in graded['weak_concepts']
            ])
        except sqlite3.Error:
            conn.execute('ROLLBACK TO save_submission')
            conn.execute('RELEASE save_submission')
            message = 'Could not save the submission. No score was recorded.'
            conn.execute("UPDATE study_sessions SET status = 'failed', failure_reason = ? WHERE session_id = ?",
                         (message, session_id))
            return dict(session_id=session_id, status='failed', next_step=None, graded=None, error=message)
        conn.execute('RELEASE save_submission')
    # Feedback, retry eligibility/reservation, and final summary are next steps.
    return dict(session_id=session_id, quiz_id=quiz_id, attempt_id=attempt_id, result_id=result_id,
                status='grading', next_step='assessment_feedback', graded=graded, error=None)
