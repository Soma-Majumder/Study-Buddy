"""Retrieve the current reviewed quiz without publication or agent dispatch."""
from contextlib import contextmanager

from study_buddy.answer_keys import get_client_quiz, new_id
from study_buddy.quality_publication import _approved_candidate, _check_published_quiz
from study_buddy.quality_state import QualityStateRejected


class QuizRetrievalRejected(ValueError):
    pass


@contextmanager
def _read_snapshot(conn):
    # A savepoint gives all audit reads one SQLite snapshot. Nesting preserves
    # caller transactions; no commit/rollback of unrelated caller work occurs.
    name = new_id('quiz_read')
    conn.execute(f'SAVEPOINT {name}')
    try:
        yield
    except BaseException:
        conn.execute(f'ROLLBACK TO {name}')
        raise
    finally:
        conn.execute(f'RELEASE {name}')


def get_current_quiz(conn, learner_id, session_id):
    """Return a keyless current quiz for a trusted authenticated learner ID.

    Reuses the existing approval/content audit, but never calls the publishing
    entry point or write-lock helpers. Private evidence is read internally only;
    the return value is an explicit learner projection. Published quizzes remain
    retrievable after preparation deadlines; expiry must not erase publication.
    """
    unavailable = 'Current quiz is unavailable for this learner and session.'
    if not all(isinstance(value, str) and value.strip() for value in (learner_id, session_id)):
        raise QuizRetrievalRejected(unavailable)
    try:
        with _read_snapshot(conn):
            session = conn.execute(
                'SELECT status,lesson_id,lesson_version,learning_retry_count FROM study_sessions '
                'WHERE session_id=? AND learner_id=?', (session_id, learner_id)).fetchone()
            if session is None or session[0] != 'awaiting_answers':
                raise QuizRetrievalRejected(unavailable)
            quiz = conn.execute('SELECT quiz_id,explanation_id,round FROM quizzes WHERE session_id=? ORDER BY rowid DESC LIMIT 1',
                                (session_id,)).fetchone()
            if quiz is None or conn.execute('SELECT 1 FROM attempts WHERE quiz_id=?', (quiz[0],)).fetchone():
                raise QuizRetrievalRejected(unavailable)
            binding = conn.execute(
                'SELECT operation_id,candidate_version,candidate_hash,review_call_id '
                'FROM quiz_quality_publications WHERE quiz_id=?', (quiz[0],)).fetchone()
            if binding is None:
                raise QuizRetrievalRejected(unavailable)
            cursor = conn.execute('SELECT * FROM quiz_quality_operations WHERE operation_id=? AND learner_id=? AND session_id=?',
                                  (binding[0], learner_id, session_id))
            row = cursor.fetchone()
            if row is None:
                raise QuizRetrievalRejected(unavailable)
            op = dict(zip([column[0] for column in cursor.description], row))
            if ((op['lesson_id'],op['lesson_version']) != session[1:3]
                    or (op['explanation_id'],op['round']) != quiz[1:]
                    or session[3] != (0 if op['round']=='first' else 1)
                    or (op['candidate_version'],op['candidate_hash']) != binding[1:3]):
                raise QuizRetrievalRejected(unavailable)
            candidate, review_call_id = _approved_candidate(conn, op)
            if review_call_id != binding[3]:
                raise QuizRetrievalRejected(unavailable)
            _check_published_quiz(conn, op, candidate, quiz[0])
            return dict(session_id=session_id, status='awaiting_answers',
                        quiz=get_client_quiz(conn, quiz[0]), next_step='submit_answers')
    except (QualityStateRejected, ValueError, KeyError, TypeError):
        # Do not reveal whether an ID belongs to another learner or which
        # private approval evidence failed. Infrastructure DB errors propagate.
        raise QuizRetrievalRejected(unavailable) from None
