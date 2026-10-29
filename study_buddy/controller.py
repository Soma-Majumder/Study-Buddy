"""First routing step: approved lesson -> Tutor -> stored explanation or failure.

learner_id must come from trusted application context, not an agent. This does
not authenticate users. First and retry quizzes use the bounded quality gate. Use a connection with schema.sql
and approved lessons already loaded by study_buddy.lessons.load_lessons.
"""
import json
import os
from pathlib import Path
import re
import subprocess
from study_buddy.cancellation import run_agent_process
import sqlite3

import jsonschema

from study_buddy.answer_keys import new_id, get_answer_key
from study_buddy.grading import grade
from study_buddy.session_start_requests import reserve_session_start, StartRequestRejected, check_session_start_deadline_locked
from study_buddy.session_status import get_session_status, SessionStatusRejected
from study_buddy.quiz_retrieval import get_current_quiz, QuizRetrievalRejected
from study_buddy.feedback import explain_results
from study_buddy.routing import decide_next_step
from study_buddy.reteaching import dispatch_reteaching
from study_buddy.retry_quiz import dispatch_retry_quiz
from study_buddy.summary import finish_session
from study_buddy.summary_retrieval import get_saved_summary, SummaryRetrievalRejected
from study_buddy.assessment import run_assessment
from study_buddy.quality_dispatch import dispatch_quality_review, dispatch_quality_revision
from study_buddy.quality_publication import publish_reviewed_quiz
from study_buddy.quiz_pipeline import continue_quality_quiz
from study_buddy.quality_expiry import sweep_expired_quality_operations

ROOT = Path(__file__).resolve().parent.parent
HANDOFF_SCHEMA = json.loads((ROOT / 'schemas/v1/tutor_to_assessment.schema.json').read_text())


class TutorFailure(Exception):
    def __init__(self, stage='model call', repair_count=0, code=None):
        super().__init__('Tutor could not produce a valid explanation.')
        self.stage = stage
        self.repair_count = repair_count
        self.code = 'live_calls_disabled' if code == 'live_calls_disabled' else None


def run_tutor(context):
    """Invoke the existing JS harness. Credentials stay in the environment."""
    node = os.environ.get('STUDY_BUDDY_NODE', 'node')
    try:
        process = run_agent_process(
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
        raise TutorFailure(error.get('stage', 'provider adapter'), count if count in (0, 1) else 0, error.get('code'))
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


def start_session(conn, learner_id, lesson_id, *, tutor=run_tutor, request_id=None, lesson_version=None):
    """Return {session_id, status, next_step, handoff, error}.

    The real Tutor harness owns generation validation and its one repair.
    Controller validation is a final boundary check; it never restarts the
    harness or grants another repair. Repair outcomes are saved on return;
    the default bridge reserves each repair durably before authorizing the JS call.
    Real Tutor starts require request_id and lesson_version for the reservation path;
    only its winner calls Tutor. Injected offline runners retain the legacy no-ID fixture path.
    """
    if tutor is run_tutor and (request_id is None or lesson_version is None):
        raise StartRequestRejected('Live Tutor starts require a stable request ID and explicit lesson version.')
    if not isinstance(learner_id, str) or not re.fullmatch(r'learner_[a-z0-9]{12}', learner_id):
        raise ValueError('A controller-owned learner ID is required.')
    if not conn.execute('SELECT 1 FROM learners WHERE learner_id = ?', (learner_id,)).fetchone():
        raise ValueError('Learner is not registered.')
    reservation = None
    if request_id is not None:
        reservation = reserve_session_start(conn, learner_id, request_id, lesson_id, lesson_version)
        if not reservation['reserved']:
            return dict(session_id=reservation['session_id'], status=reservation['status'],
                        next_step=None, handoff=None, error=None, request_id=request_id, reserved=False)
        context = dict(lessonId=lesson_id, lessonVersion=lesson_version,
                       sessionId=reservation['session_id'], handoffId=reservation['handoff_id'],
                       explanationId=reservation['explanation_id'], round='first')
        lesson = conn.execute(
            'SELECT version, source_url, license, attribution FROM lessons '
            'WHERE lesson_id=? AND version=? AND approved=1', (lesson_id, lesson_version)).fetchone()
        if not lesson:
            return _fail(conn, context, 0, 'The reserved lesson is no longer approved. No quiz was started.')
    else:
        if lesson_version is not None:
            raise StartRequestRejected('An explicit lesson version requires a session-start request ID.')
        lesson = conn.execute(
            'SELECT version, source_url, license, attribution FROM lessons '
            'WHERE lesson_id = ? AND approved = 1 ORDER BY version DESC LIMIT 1', (lesson_id,),
        ).fetchone()
        if not lesson:
            raise ValueError('An approved lesson is required.')
        context = dict(lessonId=lesson_id, sessionId=new_id('session'),
                       handoffId=new_id('handoff'), explanationId=new_id('expl'), round='first')
        with conn:
            conn.execute(
                "INSERT INTO study_sessions (session_id, learner_id, lesson_id, lesson_version, status, created_at) "
                "VALUES (?, ?, ?, ?, 'teaching', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                (context['sessionId'], learner_id, lesson_id, lesson[0]),
            )
            conn.execute("INSERT INTO handoff_repairs VALUES (?, ?, 'tutor_to_assessment', 0)",
                         (context['handoffId'], context['sessionId']))
    session_id = context['sessionId']
    concepts = dict(conn.execute(
        'SELECT concept_id, name FROM concepts WHERE lesson_id = ? AND lesson_version = ?',
        (lesson_id, lesson[0]),
    ))
    repair_count = 0
    if reservation is not None:
        with conn:
            conn.execute('UPDATE study_sessions SET status=status WHERE session_id=?', (session_id,))
            allowed = check_session_start_deadline_locked(conn, learner_id, session_id)
        if not allowed:
            return dict(session_id=session_id, status='failed', next_step=None, handoff=None,
                        error='Reserved session is no longer available for Tutor.')
    try:
        if tutor is run_tutor:
            from study_buddy.tutor_bridge import run_durable_tutor
            result = run_durable_tutor(conn, context)
        else:
            result = tutor(context)
        repair_count = result['repairCount']
        if type(repair_count) is not int or repair_count not in (0, 1):
            raise ValueError('Invalid repair count.')
        handoff = result['handoff']
        _check_handoff(handoff, context, lesson, concepts)
    except Exception as error:
        count = error.repair_count if isinstance(error, TutorFailure) else repair_count
        message = ('Paid Claude calls are disabled. Enable them explicitly in trusted backend configuration.'
                   if isinstance(error, TutorFailure) and error.code == 'live_calls_disabled' else
                   'Tutor did not return a valid explanation. No quiz was started.')
        return _fail(conn, context, count if type(count) is int and count in (0, 1) else 0, message)

    try:
        with conn:
            if reservation is not None:
                conn.execute('UPDATE study_sessions SET status=status WHERE session_id=?', (session_id,))
                allowed = check_session_start_deadline_locked(conn, learner_id, session_id)
            else:
                allowed = True
            if not allowed:
                # Commit expiry before returning; discard all late Tutor content.
                return dict(session_id=session_id, status='failed', next_step=None, handoff=None,
                            error='Tutor result arrived after the reserved start became unavailable.')
            conn.execute('INSERT INTO explanations VALUES (?, ?, ?, ?)',
                         (context['explanationId'], session_id, 'first', handoff['payload']['explanation_text']))
            conn.executemany('INSERT INTO explanation_concepts VALUES (?, ?)',
                             [(context['explanationId'], c['concept_id']) for c in handoff['payload']['concepts']])
            conn.execute('UPDATE handoff_repairs SET repair_count = ? WHERE handoff_id = ?',
                         (repair_count, context['handoffId']))
    except Exception:
        return _fail(conn, context, repair_count, 'Could not save the explanation. No quiz was started.')
    # A quiz does not exist yet: keep teaching status, never awaiting_answers or completed.
    return dict(session_id=session_id, status='teaching', next_step='assessment', handoff=handoff, error=None,
                **({'request_id': request_id, 'reserved': True} if reservation is not None else {}))


def _fail(conn, context, repair_count, message):
    # If recording failure itself fails, propagate the DB error instead of claiming a save.
    with conn:
        conn.execute("UPDATE study_sessions SET status = 'failed', failure_reason = ? WHERE session_id = ?",
                     (message, context['sessionId']))
        conn.execute('UPDATE handoff_repairs SET repair_count = ? WHERE handoff_id = ?',
                     (repair_count, context['handoffId']))
    return dict(session_id=context['sessionId'], status='failed', next_step=None, handoff=None, error=message)

def start_quiz_session(conn, learner_id, lesson_id, *, assessment=run_assessment, tutor=run_tutor,
                       reviewer=None, revision_assessment=None, request_id=None, lesson_version=None):
    """Tutor -> bounded generation/review/revision -> exact approved publication."""
    from study_buddy.quiz_pipeline import start_quiz_pipeline
    from study_buddy.reviewer import run_reviewer
    from study_buddy.assessment import run_assessment_revision
    started = start_session(conn, learner_id, lesson_id, tutor=tutor, request_id=request_id, lesson_version=lesson_version)
    if started.get('reserved') is False:
        return dict(started, quiz=None)
    if started['status'] == 'failed': return started
    return start_quiz_pipeline(conn, learner_id, started['handoff'], assessment=assessment,
                               reviewer=reviewer or run_reviewer,
                               revision_assessment=revision_assessment or run_assessment_revision)


class SubmissionRejected(ValueError):
    """Invalid, unauthorized, stale, or already-submitted learner input."""


def _saved_submission(conn, learner_id, quiz_id, session_id, status, answers):
    rows = conn.execute('SELECT attempt_id FROM attempts WHERE quiz_id=? AND learner_id=?',
                        (quiz_id, learner_id)).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise SubmissionRejected('Saved submission evidence is inconsistent.')
    attempt_id = rows[0][0]
    saved_answers = dict(conn.execute('SELECT question_id, selected_option FROM responses WHERE attempt_id=?', (attempt_id,)))
    if answers != saved_answers:
        raise SubmissionRejected('This quiz was already submitted with different answers.')
    result = conn.execute('SELECT result_id, score, passed FROM results WHERE attempt_id=?', (attempt_id,)).fetchone()
    items = [dict(position=row[0], question_id=row[1], concept_id=row[2], selected=row[3],
                  correct_option=row[4], is_correct=bool(row[5]), rationale=row[6]) for row in conn.execute(
        'SELECT q.position,q.question_id,q.concept_id,r.selected_option,k.correct_option,r.is_correct,k.rationale '
        'FROM questions q JOIN responses r ON r.question_id=q.question_id AND r.attempt_id=? '
        'JOIN answer_keys k ON k.question_id=q.question_id WHERE q.quiz_id=? ORDER BY q.position',
        (attempt_id, quiz_id))]
    if result is None or len(items) != 10 or len(saved_answers) != 10 or sum(item['is_correct'] for item in items) != result[1]:
        raise SubmissionRejected('Saved submission evidence is incomplete; no new attempt was created.')
    weak = [dict(concept_id=row[0], missed_count=row[1]) for row in conn.execute(
        'SELECT concept_id,missed_count FROM weak_concepts WHERE source_attempt_id=? AND learner_id=? '
        'ORDER BY missed_count DESC,concept_id', (attempt_id, learner_id))]
    # Read saved evidence only: never grade again, reserve retries, or rewind state.
    feedback_started = conn.execute('SELECT 1 FROM feedback_operations WHERE attempt_id=?', (attempt_id,)).fetchone()
    return dict(session_id=session_id, quiz_id=quiz_id, attempt_id=attempt_id, result_id=result[0],
                status=status, next_step='assessment_feedback' if status == 'grading' and not feedback_started else None,
                graded=dict(score=result[1], passed=bool(result[2]), items=items, weak_concepts=weak), error=None)


def submit_answers(conn, learner_id, quiz_id, answers):
    """Save once; identical retries read saved evidence, changed answers conflict.

    Serialize ownership/state/evidence reads with the claim so concurrent callers
    see the winner's committed attempt before deciding whether they are replays.
    """
    with conn:
        owned = conn.execute(
            'UPDATE study_sessions SET status=status WHERE learner_id=? AND session_id IN '
            '(SELECT session_id FROM quizzes WHERE quiz_id=?)', (learner_id, quiz_id)).rowcount
        if not owned:
            raise SubmissionRejected('Quiz is unavailable for this learner.')
        return _submit_answers_locked(conn, learner_id, quiz_id, answers)


def _submit_answers_locked(conn, learner_id, quiz_id, answers):
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
    questions = {r[0] for r in conn.execute('SELECT question_id FROM questions WHERE quiz_id = ?', (quiz_id,))}
    if (not isinstance(answers, dict) or set(answers) != questions or
            any(not isinstance(v, str) or v not in ('a', 'b', 'c', 'd') for v in answers.values())):
        raise SubmissionRejected('Answer every quiz question with one option from a to d.')
    saved = _saved_submission(conn, learner_id, quiz_id, session_id, status, answers)
    if saved is not None:
        return saved
    if status != 'awaiting_answers':
        raise SubmissionRejected('This session is not accepting answers.')
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
