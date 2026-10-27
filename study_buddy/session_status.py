"""Learner-scoped progress snapshot; SELECT only, no agent or recovery dispatch."""


class SessionStatusRejected(ValueError):
    pass


STAGES = {
    'started': 'preparing', 'teaching': 'preparing',
    'awaiting_answers': 'awaiting_answers', 'grading': 'grading',
    'reteaching': 'reteaching', 'pending_save': 'pending_save',
    'completed': 'completed', 'failed': 'failed',
}


def get_session_status(conn, learner_id, session_id):
    """Return one consistent SQL snapshot for a trusted authenticated learner ID.

    No key/teaching/candidate/report/error text is read. Availability is metadata,
    not permission to fetch a quiz without the publication gate. Does not sweep
    expired operations, commit/rollback caller work, or initiate recovery.
    next_action is advisory; its target must recheck ownership/readiness.
    """
    if not all(isinstance(value, str) and value.strip() for value in (learner_id, session_id)):
        raise SessionStatusRejected('Session is unavailable for this learner.')
    row = conn.execute(
        'SELECT s.session_id,s.status,s.learning_retry_count, '
        '(SELECT q.quiz_id FROM quizzes q '
        'JOIN quiz_quality_publications p ON p.quiz_id=q.quiz_id '
        'JOIN quiz_quality_operations o ON o.operation_id=p.operation_id '
        'WHERE q.session_id=s.session_id AND o.session_id=s.session_id AND o.learner_id=s.learner_id '
        'AND o.explanation_id=q.explanation_id AND o.round=q.round '
        'AND q.rowid=(SELECT MAX(latest.rowid) FROM quizzes latest WHERE latest.session_id=s.session_id) '
        'AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.quiz_id=q.quiz_id)), '
        'EXISTS(SELECT 1 FROM session_summaries final WHERE final.session_id=s.session_id) '
        'FROM study_sessions s WHERE s.session_id=? AND s.learner_id=?',
        (session_id, learner_id)).fetchone()
    if row is None:
        raise SessionStatusRejected('Session is unavailable for this learner.')
    sid, status, retry_count, quiz_id, saved_summary = row
    quiz_available = status == 'awaiting_answers' and quiz_id is not None
    summary_available = status == 'completed' and bool(saved_summary)
    message = None
    if status == 'failed':
        message = 'This study session could not finish because of a technical problem.'
    elif status == 'pending_save':
        message = 'Your progress is saved; the final summary is still waiting to be saved.'
    elif status == 'completed' and not summary_available:
        message = 'The saved summary is unavailable. Please contact support.'
    elif status == 'awaiting_answers' and not quiz_available:
        message = 'No unanswered published quiz is available for this session.'
    # Choose advice from the same SELECT snapshot; never dispatch the action.
    # Metadata availability still requires the retrieval helper's full checks.
    if status == 'awaiting_answers':
        next_action = 'get_current_quiz' if quiz_available else 'contact_support'
    elif status == 'completed':
        next_action = 'get_saved_summary' if summary_available else 'contact_support'
    elif status == 'pending_save':
        next_action = 'resume_summary'
    elif status == 'failed':
        next_action = 'start_new_session'
    else:
        next_action = 'read_session_status'
    return dict(session_id=sid, status=status, stage=STAGES[status],
                learning_retry_count=retry_count, quiz_available=quiz_available,
                quiz_id=quiz_id if quiz_available else None,
                summary_available=summary_available, message=message, next_action=next_action)
