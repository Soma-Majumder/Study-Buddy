"""Durable quality-operation reservations, without model calls or publication.

These are trusted controller primitives. complete_quality_call must receive
controller-validated review outcomes, never raw model or learner decisions.
quality_storage composes these primitives with private payload/report saves.
Controller/model wiring and publication remain separate work.
"""
from contextlib import nullcontext
import hashlib
import json
import time

from scripts.validate_quiz import check as check_quiz
from study_buddy.answer_keys import new_id
from study_buddy.quality_review import candidate_hash

KINDS = {'generation', 'technical_repair', 'review', 'revision', 'review_revision'}


class QualityStateRejected(ValueError):
    pass


def _operation(conn, learner_id, operation_id):
    # No-op write takes SQLite's lock before any budget/state reads.
    owned = conn.execute('UPDATE quiz_quality_operations SET stage = stage WHERE operation_id = ? AND learner_id = ? '
                         'AND session_id IN (SELECT session_id FROM study_sessions WHERE learner_id = ?)',
                         (operation_id, learner_id, learner_id)).rowcount
    if not owned:
        raise QualityStateRejected('Quality operation is unavailable for this learner.')
    cursor = conn.execute('SELECT * FROM quiz_quality_operations WHERE operation_id = ?', (operation_id,))
    return dict(zip([c[0] for c in cursor.description], cursor.fetchone()))


def _snapshot(conn, learner_id, operation_id, **extra):
    operation = _operation(conn, learner_id, operation_id)
    count = conn.execute('SELECT COUNT(*) FROM quiz_quality_calls WHERE operation_id = ?', (operation_id,)).fetchone()[0]
    return dict(operation=operation, reserved_call_count=count, **extra)


def _fail(conn, operation_id, code):
    conn.execute("UPDATE quiz_quality_operations SET stage = 'failed', failure_code = ? WHERE operation_id = ?", (code, operation_id))


def _active(conn, operation):
    row = conn.execute('SELECT status, learning_retry_count, lesson_id, lesson_version FROM study_sessions WHERE session_id = ?', (operation['session_id'],)).fetchone()
    expected = 'teaching' if operation['round'] == 'first' else 'reteaching'
    if not row or row != (expected, 0 if operation['round'] == 'first' else 1, operation['lesson_id'], operation['lesson_version']) or conn.execute('SELECT 1 FROM quizzes WHERE session_id = ? AND round = ?',
                                                   (operation['session_id'], operation['round'])).fetchone():
        raise QualityStateRejected('Session is no longer preparing this quiz round.')


def start_quality_operation(conn, learner_id, session_id, handoff_id, explanation_id, round_):
    """Reuse one operation per session/round; its deadline/counters never reset."""
    with conn:
        owned = conn.execute('UPDATE study_sessions SET status = status WHERE session_id = ? AND learner_id = ?',
                             (session_id, learner_id)).rowcount
        if not owned:
            raise QualityStateRejected('Session is unavailable for this learner.')
        previous = conn.execute('SELECT operation_id, handoff_id, explanation_id FROM quiz_quality_operations '
                                'WHERE session_id = ? AND round = ?', (session_id, round_)).fetchone()
        if previous:
            if previous[1:] != (handoff_id, explanation_id):
                raise QualityStateRejected('Existing operation has a different handoff or explanation.')
            return _snapshot(conn, learner_id, previous[0], created=False)
        if round_ not in ('first', 'retry'):
            raise QualityStateRejected('Unknown quiz round.')
        if not conn.execute("SELECT 1 FROM handoff_repairs WHERE handoff_id = ? AND session_id = ? AND direction = 'tutor_to_assessment'",
                            (handoff_id, session_id)).fetchone():
            raise QualityStateRejected('A saved outgoing Tutor handoff is required.')
        if not conn.execute('SELECT 1 FROM explanations WHERE explanation_id = ? AND session_id = ? AND round = ?',
                            (explanation_id, session_id, round_)).fetchone():
            raise QualityStateRejected('A saved explanation for this round is required.')
        lesson_id, lesson_version = conn.execute('SELECT lesson_id, lesson_version FROM study_sessions WHERE session_id = ?', (session_id,)).fetchone()
        op = dict(session_id=session_id, round=round_, lesson_id=lesson_id, lesson_version=lesson_version)
        _active(conn, op)
        operation_id, now = new_id('quality'), time.time()
        conn.execute('INSERT INTO quiz_quality_operations '
                     '(operation_id, session_id, learner_id, lesson_id, lesson_version, handoff_id, explanation_id, round, stage, started_at, deadline_at) '
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'generating', ?, ?)",
                     (operation_id, session_id, learner_id, lesson_id, lesson_version, handoff_id, explanation_id, round_, now, now + 180))
        return _snapshot(conn, learner_id, operation_id, created=True)


def reserve_quality_call(conn, learner_id, operation_id, kind):
    """Only reserved=True authorizes a new dispatch; duplicates never do."""
    if kind not in KINDS:
        raise QualityStateRejected('Unknown quality call kind.')
    with conn:
        op = _operation(conn, learner_id, operation_id)
        previous = conn.execute('SELECT call_id FROM quiz_quality_calls WHERE operation_id = ? AND kind = ?',
                                (operation_id, kind)).fetchone()
        if previous:
            return _snapshot(conn, learner_id, operation_id, reserved=False, call_id=previous[0])
        if op['stage'] in ('approved', 'failed'):
            raise QualityStateRejected('Quality operation is terminal.')
        _active(conn, op)
        now = time.time()
        remaining = op['deadline_at'] - now
        if remaining <= 1:
            _fail(conn, operation_id, 'quality_deadline')
            return _snapshot(conn, learner_id, operation_id, reserved=False, call_id=None)
        expected = {'generation': 'generating', 'review': 'reviewing', 'revision': 'needs_revision',
                    'review_revision': 'reviewing_revision'}
        if kind == 'technical_repair':
            if op['stage'] not in ('generation_invalid', 'revision_invalid'):
                raise QualityStateRejected('Technical correction is not pending.')
            claimed = conn.execute('UPDATE handoff_repairs SET repair_count = 1 WHERE handoff_id = ? AND repair_count = 0',
                                   (op['handoff_id'],)).rowcount
            if not claimed:
                _fail(conn, operation_id, 'technical_repair_exhausted')
                return _snapshot(conn, learner_id, operation_id, reserved=False, call_id=None)
            stage = 'generating' if op['candidate_version'] == 0 else 'revising'
            conn.execute('UPDATE quiz_quality_operations SET stage = ? WHERE operation_id = ?', (stage, operation_id))
        else:
            if op['stage'] != expected[kind]:
                raise QualityStateRejected('Call is out of sequence.')
            if kind == 'revision':
                conn.execute("UPDATE quiz_quality_operations SET stage = 'revising', revision_count = 1, "
                             'candidate_version = 1, candidate_hash = NULL WHERE operation_id = ? AND revision_count = 0', (operation_id,))
            elif kind in ('review', 'review_revision'):
                if not op['candidate_hash']:
                    raise QualityStateRejected('A valid candidate snapshot is required.')
                conn.execute('UPDATE quiz_quality_operations SET review_count = review_count + 1 WHERE operation_id = ?', (operation_id,))
        op = _operation(conn, learner_id, operation_id)
        call_id = new_id('quality_call')
        conn.execute('INSERT INTO quiz_quality_calls (call_id, operation_id, kind, status, candidate_version, candidate_hash, reserved_at) '
                     "VALUES (?, ?, ?, 'reserved', ?, ?, ?)",
                     (call_id, operation_id, kind, op['candidate_version'], op['candidate_hash'], now))
        return _snapshot(conn, learner_id, operation_id, reserved=True, call_id=call_id,
                         provider_timeout_ms=min(60000, int(remaining * 1000) - 1000),
                         process_timeout_seconds=min(70, remaining))


def complete_quality_call(conn, learner_id, call_id, *, outcome, candidate=None, reviewed_hash=None, manage_transaction=True):
    """Record a result once. Review outcomes must already pass validate_review.

    This low-level primitive stores hashes/state only; prefer quality_storage
    for atomic payload/report completion. manage_transaction=False is internal
    to a caller-owned transaction, never a public/model parameter.
    """
    with conn if manage_transaction else nullcontext():
        call = conn.execute('SELECT operation_id, kind, status, candidate_version, candidate_hash, result_fingerprint '
                            'FROM quiz_quality_calls WHERE call_id = ?', (call_id,)).fetchone()
        if not call:
            raise QualityStateRejected('Unknown reserved call.')
        operation_id, kind, status, version, bound_hash, previous_fingerprint = call
        op = _operation(conn, learner_id, operation_id)
        is_review = kind in ('review', 'review_revision')
        allowed = {'pass', 'needs_revision', 'blocked', 'error'} if is_review else {'valid', 'invalid', 'error'}
        if outcome not in allowed:
            raise QualityStateRejected('Unexpected result for this call kind.')
        digest = None
        if outcome == 'valid':
            # The controller's saved explanation concepts define the allowed scope.
            concepts = [{'concept_id': r[0]} for r in conn.execute('SELECT concept_id FROM explanation_concepts WHERE explanation_id = ?',
                                                                 (op['explanation_id'],))]
            handoff = dict(handoff_id=op['handoff_id'], context=dict(round=op['round']),
                           payload=dict(explanation_id=op['explanation_id'], concepts=concepts))
            if op['round'] == 'retry': handoff['payload']['focus_concept_ids'] = [c['concept_id'] for c in concepts]
            if check_quiz(candidate, handoff):
                raise QualityStateRejected('Valid outcome requires a contract-valid candidate.')
            digest = candidate_hash(candidate)
        if is_review and outcome != 'error':
            if reviewed_hash != bound_hash or reviewed_hash != op['candidate_hash'] or version != op['candidate_version']:
                raise QualityStateRejected('Review outcome does not match the reserved candidate.')
            digest = reviewed_hash
        fingerprint = hashlib.sha256(json.dumps(dict(outcome=outcome, candidate_hash=digest), sort_keys=True).encode()).hexdigest()
        if status != 'reserved':
            if fingerprint != previous_fingerprint:
                raise QualityStateRejected('Completed call cannot be overwritten.')
            return _snapshot(conn, learner_id, operation_id, applied=False)
        if op['stage'] in ('approved', 'failed'):
            raise QualityStateRejected('Quality operation is terminal.')
        _active(conn, op)
        now = time.time()
        if now >= op['deadline_at']:
            _fail(conn, operation_id, 'quality_deadline')
            conn.execute("UPDATE quiz_quality_calls SET status = 'failed', outcome = 'deadline', finished_at = ? WHERE call_id = ?", (now, call_id))
            return _snapshot(conn, learner_id, operation_id, applied=False)
        failure = None
        if outcome in ('error', 'blocked'):
            stage, failure = 'failed', 'provider_failed' if outcome == 'error' else 'review_uncertainty'
        elif outcome == 'valid':
            stage = 'reviewing' if version == 0 else 'reviewing_revision'
        elif outcome == 'invalid':
            repair_used = conn.execute('SELECT repair_count FROM handoff_repairs WHERE handoff_id = ?', (op['handoff_id'],)).fetchone()[0]
            stage = 'failed' if repair_used else ('generation_invalid' if version == 0 else 'revision_invalid')
            if repair_used: failure = 'technical_repair_exhausted'
        elif outcome == 'pass': stage = 'approved'
        else:
            stage = 'needs_revision' if version == 0 else 'failed'
            if version == 1: failure = 'content_revision_exhausted'
        conn.execute('UPDATE quiz_quality_operations SET stage = ?, candidate_hash = COALESCE(?, candidate_hash), failure_code = ? '
                     'WHERE operation_id = ?', (stage, digest, failure, operation_id))
        conn.execute("UPDATE quiz_quality_calls SET status = ?, outcome = ?, finished_at = ?, result_fingerprint = ? WHERE call_id = ?",
                     ('failed' if outcome == 'error' else 'completed', outcome, now, fingerprint, call_id))
        return _snapshot(conn, learner_id, operation_id, applied=True)
