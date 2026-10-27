"""Drive one study session end to end: Tutor -> quiz -> grade -> feedback -> reteach -> one retry -> summary.

This only sequences the existing controller steps. Every step keeps its own saved
state, budgets and safe failure handling; nothing here grades, reserves a retry,
or touches the database directly. Callers supply answer_quiz(client_quiz), which
returns {question_id: 'a'|'b'|'c'|'d'} for a keyless learner quiz.
"""
from study_buddy.controller import (decide_next_step, dispatch_reteaching, dispatch_retry_quiz, explain_results,
                                    finish_session, run_tutor, start_quiz_session, submit_answers)
from study_buddy.assessment import run_assessment
from study_buddy.feedback_runner import run_feedback


def _stopped(session_id, steps, error, status='failed'):
    return dict(session_id=session_id, status=status, steps=steps, scores=[s['score'] for s in steps if 'score' in s],
                summary=None, error=error)


def run_study_session(conn, learner_id, lesson_id, answer_quiz, *, tutor=run_tutor, assessment=run_assessment,
                      reviewer=None, revision_assessment=None, feedback=run_feedback, request_id=None, lesson_version=None):
    """Return {session_id, status, steps, scores, summary, error}. status is 'completed' only after a saved summary."""
    steps = []
    started = start_quiz_session(conn, learner_id, lesson_id, assessment=assessment, tutor=tutor,
                                 reviewer=reviewer, revision_assessment=revision_assessment,
                                 request_id=request_id, lesson_version=lesson_version)
    session_id = started.get('session_id')
    steps.append(dict(step='quiz', round='first', status=started['status'], error=started.get('error')))
    if started.get('reserved') is False:
        return dict(session_id=session_id, status=started['status'], steps=steps, scores=[], summary=None,
                    error=None, next_action='read_session_status', request_id=request_id, reserved=False)
    quiz = started.get('quiz')
    if quiz is None:
        return _stopped(session_id, steps, started.get('error') or 'No quiz was published.')
    for round_ in ('first', 'retry'):
        submitted = submit_answers(conn, learner_id, quiz['quiz_id'], answer_quiz(quiz))
        steps.append(dict(step='grade', round=round_, status=submitted['status'], error=submitted.get('error'),
                          **({'score': submitted['graded']['score']} if submitted.get('graded') else {})))
        if submitted['status'] == 'failed':
            return _stopped(session_id, steps, submitted['error'])
        explained = explain_results(conn, learner_id, submitted['attempt_id'], assessment=feedback)
        steps.append(dict(step='feedback', round=round_, status=explained['status'], error=explained.get('error')))
        if explained['status'] == 'failed':
            return _stopped(session_id, steps, explained['error'])
        route = decide_next_step(conn, learner_id, submitted['attempt_id'])
        steps.append(dict(step='route', round=round_, status=route['status'], next_step=route['next_step']))
        if route['next_step'] == 'reteach':
            retaught = dispatch_reteaching(conn, learner_id, submitted['attempt_id'], tutor=tutor)
            steps.append(dict(step='reteach', round=round_, status=retaught['status'], error=retaught.get('error')))
            if retaught['status'] == 'failed' or retaught['next_step'] != 'assessment':
                return _stopped(session_id, steps, retaught.get('error') or 'Reteaching did not finish.', retaught['status'])
            retry = dispatch_retry_quiz(conn, learner_id, submitted['attempt_id'], assessment=assessment,
                                        reviewer=reviewer, revision_assessment=revision_assessment)
            steps.append(dict(step='quiz', round='retry', status=retry['status'], error=retry.get('error')))
            quiz = retry.get('quiz')
            if quiz is None:
                return _stopped(session_id, steps, retry.get('error') or 'No retry quiz was published.', retry['status'])
            continue
        done = finish_session(conn, learner_id, session_id)
        steps.append(dict(step='summary', round=round_, status=done['status'], error=done.get('error')))
        if done['status'] != 'completed':
            return _stopped(session_id, steps, done.get('error') or 'The summary was not saved.', done['status'])
        return dict(session_id=session_id, status='completed', steps=steps,
                    scores=[s['score'] for s in steps if 'score' in s], summary=done['summary'], error=None)
    return _stopped(session_id, steps, 'The session did not reach a summary.')
