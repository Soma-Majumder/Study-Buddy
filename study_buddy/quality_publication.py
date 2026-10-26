"""Publish only an exact, saved, reviewed candidate in one transaction."""
import sqlite3
import hashlib
import json
import time

from study_buddy.answer_keys import new_id, save_quiz, get_client_quiz
from study_buddy.quality_state import QualityStateRejected, _operation, _active
from study_buddy.quality_storage import _load_candidate, _load_report
from study_buddy.quality_review import candidate_hash


def _approved_candidate(conn, op):
    if op['stage'] != 'approved' or op['failure_code'] is not None:
        raise QualityStateRejected('A saved passing review is required before publication.')
    version, digest = op['candidate_version'], op['candidate_hash']
    candidate, _, _ = _load_candidate(conn, op, version)
    if candidate_hash(candidate) != digest:
        raise QualityStateRejected('Approval does not match the saved candidate.')
    row = conn.execute('SELECT r.review_call_id, r.candidate_hash, r.decision, c.kind, c.status, c.outcome, '
                       'c.candidate_version, c.candidate_hash, c.operation_id FROM quiz_quality_reports r '
                       'JOIN quiz_quality_calls c ON c.call_id=r.review_call_id '
                       'WHERE r.operation_id=? AND r.candidate_version=?', (op['operation_id'], version)).fetchone()
    expected_kind = 'review' if version == 0 else 'review_revision'
    if not row or row[1:] != (digest, 'pass', expected_kind, 'completed', 'pass', version, digest, op['operation_id']):
        raise QualityStateRejected('The candidate lacks a completed matching passing review.')
    _load_report(conn, op, row[0])  # Recheck evidence, report fingerprint and derived decision.
    generating = conn.execute('SELECT c.operation_id, c.candidate_version, c.result_fingerprint, c.status, c.outcome '
                              'FROM quiz_quality_candidates q JOIN quiz_quality_calls c ON c.call_id=q.generating_call_id '
                              'WHERE q.operation_id=? AND q.candidate_version=?', (op['operation_id'], version)).fetchone()
    fingerprint = hashlib.sha256(json.dumps(dict(outcome='valid', candidate_hash=digest), sort_keys=True).encode()).hexdigest()
    if generating != (op['operation_id'], version, fingerprint, 'completed', 'valid'):
        raise QualityStateRejected('The staged candidate lacks a matching completed generator call.')
    if not conn.execute('SELECT 1 FROM lessons WHERE lesson_id=? AND version=? AND approved=1',
                        (op['lesson_id'], op['lesson_version'])).fetchone():
        raise QualityStateRejected('The lesson source is no longer approved.')
    return candidate, row[0]


def _check_published_quiz(conn, op, candidate, quiz_id):
    metadata = conn.execute('SELECT session_id, explanation_id, round FROM quizzes WHERE quiz_id=?', (quiz_id,)).fetchone()
    if metadata != (op['session_id'], op['explanation_id'], op['round']):
        raise QualityStateRejected('Published quiz metadata does not match its approval.')
    rows = conn.execute('SELECT q.position, q.concept_id, q.prompt, q.option_a, q.option_b, q.option_c, q.option_d, '
                        'k.correct_option, k.rationale FROM questions q LEFT JOIN answer_keys k ON k.question_id=q.question_id '
                        'WHERE q.quiz_id=? ORDER BY q.position', (quiz_id,)).fetchall()
    keys = {key['position']: key for key in candidate['answer_key']}
    expected = [(q['position'], q['concept_id'], q['prompt'], *(q['options'][letter] for letter in 'abcd'),
                 keys[q['position']]['correct_option'], keys[q['position']]['rationale'])
                for q in sorted(candidate['quiz']['questions'], key=lambda q: q['position'])]
    if rows != expected:
        raise QualityStateRejected('Published questions or keys differ from the approved candidate.')


def _response(conn, op, quiz_id, *, published):
    status = conn.execute('SELECT status FROM study_sessions WHERE session_id=?', (op['session_id'],)).fetchone()[0]
    # An old first-round publication must not become the active retry quiz.
    latest = conn.execute('SELECT quiz_id FROM quizzes WHERE session_id=? ORDER BY rowid DESC LIMIT 1', (op['session_id'],)).fetchone()
    visible = status == 'awaiting_answers' and latest == (quiz_id,)
    return dict(operation_id=op['operation_id'], session_id=op['session_id'], status=status,
                published=published, quiz=get_client_quiz(conn, quiz_id) if visible else None,
                next_step='submit_answers' if visible else None, error=None)


def publish_reviewed_quiz(conn, learner_id, operation_id):
    """Owner-scoped, idempotent publication. No model calls or caller quiz payload.

    A failed save rolls back quiz, questions, keys, binding and ready status.
    The approved candidate stays available for retrying the save, without a
    fresh model call or budget. Failure to record a save error propagates.
    """
    try:
        with conn:
            op = _operation(conn, learner_id, operation_id)  # Lock before reading approval/publication.
            candidate, review_call_id = _approved_candidate(conn, op)
            previous = conn.execute('SELECT candidate_version, candidate_hash, review_call_id, quiz_id '
                                    'FROM quiz_quality_publications WHERE operation_id=?', (operation_id,)).fetchone()
            if previous:
                if previous[:3] != (op['candidate_version'], op['candidate_hash'], review_call_id):
                    raise QualityStateRejected('Publication binding does not match the approval.')
                _check_published_quiz(conn, op, candidate, previous[3])
                return _response(conn, op, previous[3], published=False)
            _active(conn, op)
            if time.time() >= op['deadline_at']:
                raise QualityStateRejected('The quality operation deadline expired before publication.')
            quiz_id = new_id('quiz')
            save_quiz(conn, quiz_id, op['session_id'], op['explanation_id'], candidate, manage_transaction=False)
            _check_published_quiz(conn, op, candidate, quiz_id)
            if time.time() >= op['deadline_at']:
                raise QualityStateRejected('The quality operation deadline expired during publication.')
            conn.execute('INSERT INTO quiz_quality_publications VALUES (?, ?, ?, ?, ?, ?)',
                         (operation_id, op['candidate_version'], op['candidate_hash'], review_call_id, quiz_id, time.time()))
            conn.execute("UPDATE study_sessions SET status='awaiting_answers', failure_reason=NULL WHERE session_id=?", (op['session_id'],))
            if op['round']=='retry':
                conn.execute("UPDATE retry_quiz_operations SET status='ready', quiz_id=?, error=NULL "
                             "WHERE session_id=? AND handoff_id=? AND status='running'",
                             (quiz_id,op['session_id'],op['handoff_id']))
            return _response(conn, op, quiz_id, published=True)
    except sqlite3.Error:
        # The publication transaction has rolled back; only now record the error.
        with conn:
            op = _operation(conn, learner_id, operation_id)
            expected = 'teaching' if op['round']=='first' else 'reteaching'
            if conn.execute('SELECT 1 FROM quiz_quality_publications WHERE operation_id=?', (operation_id,)).fetchone():
                raise  # Do not disguise a failed replay/audit as a new save failure.
            conn.execute('UPDATE study_sessions SET failure_reason=? WHERE session_id=? AND status=?',
                         ('quality_publish_save_failed', op['session_id'], expected))
            status = conn.execute('SELECT status FROM study_sessions WHERE session_id=?', (op['session_id'],)).fetchone()[0]
        return dict(operation_id=operation_id, session_id=op['session_id'], status=status, published=False,
                    quiz=None, next_step='publish_reviewed_quiz', error='quality_publish_save_failed')
