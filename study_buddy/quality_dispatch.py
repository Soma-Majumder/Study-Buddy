"""Controller-owned, once-only Reviewer dispatch for staged quiz candidates."""
import copy
import hashlib
import json
import sqlite3
import time

from study_buddy.quality_state import QualityStateRejected, _operation, reserve_quality_call
from study_buddy.quality_storage import build_private_reviewer_context, store_review_report
from study_buddy.quality_review import ReviewRejected
from study_buddy.reviewer import run_reviewer, ReviewerFailure


def _result(conn, learner_id, operation_id, *, dispatched=False, call_id=None):
    with conn:
        op = _operation(conn, learner_id, operation_id)
        return dict(operation_id=operation_id, session_id=op['session_id'], stage=op['stage'],
                    dispatched=dispatched, call_id=call_id, error=op['failure_code'],
                    next_step={'approved': 'publish_reviewed_quiz', 'needs_revision': 'revise_quiz'}.get(op['stage']))


def _fail_quality_call(conn, learner_id, operation_id, call_id, code):
    """Atomic terminal failure. Callers supply fixed codes, never model prose.

    If this save fails, propagate the database error; never claim it was saved.
    A stale session is not overwritten. A completed call cannot be replaced.
    """
    with conn:
        op = _operation(conn, learner_id, operation_id)
        call = conn.execute('SELECT status, kind FROM quiz_quality_calls WHERE call_id = ? AND operation_id = ?',
                            (call_id, operation_id)).fetchone()
        if not call or call[1] not in ('review', 'review_revision', 'revision', 'technical_repair'):
            raise QualityStateRejected('A reserved quality call is required.')
        if call[0] != 'reserved':
            raise QualityStateRejected('Completed quality calls cannot be overwritten.')
        now = time.time()
        if now >= op['deadline_at']:
            code = 'quality_deadline'
        expected = ('reviewing', 'reviewing_revision') if call[1] in ('review', 'review_revision') else ('revising',)
        if op['stage'] not in expected:
            raise QualityStateRejected('Quality operation no longer accepts this call.')
        fingerprint = hashlib.sha256(json.dumps(dict(outcome='error', candidate_hash=None), sort_keys=True).encode()).hexdigest()
        conn.execute("UPDATE quiz_quality_calls SET status = 'failed', outcome = 'error', finished_at = ?, "
                     'result_fingerprint = ? WHERE call_id = ?', (now, fingerprint, call_id))
        conn.execute("UPDATE quiz_quality_operations SET stage = 'failed', failure_code = ? WHERE operation_id = ?",
                     (code, operation_id))
        _fail_preparing_session(conn, op, code)


def _fail_preparing_session(conn, op, code):
    # Also used after store_review_report atomically records uncertainty/exhaustion.
    expected = 'teaching' if op['round'] == 'first' else 'reteaching'
    conn.execute("UPDATE study_sessions SET status = 'failed', failure_reason = ? "
                 'WHERE session_id = ? AND learner_id = ? AND status = ? AND lesson_id = ? AND lesson_version = ? '
                 'AND NOT EXISTS (SELECT 1 FROM quizzes WHERE session_id = ? AND round = ?)',
                 (code, op['session_id'], op['learner_id'], expected, op['lesson_id'], op['lesson_version'],
                  op['session_id'], op['round']))


def dispatch_quality_review(conn, learner_id, operation_id, *, reviewer=run_reviewer):
    """Reserve -> one Reviewer call -> validate/store or terminal failure.

    Repeated/concurrent entry never resends an existing reservation, including
    after a crash. Reserved calls require explicit reconciliation, not replay.
    No database transaction spans the provider call. Does not revise or publish.
    """
    with conn:
        op = _operation(conn, learner_id, operation_id)
        if op['stage'] in ('approved', 'failed', 'needs_revision'):
            if op['stage'] == 'failed':
                _fail_preparing_session(conn, op, op['failure_code'])
            return _result(conn, learner_id, operation_id)
        if op['stage'] not in ('reviewing', 'reviewing_revision'):
            raise QualityStateRejected('A saved candidate awaiting review is required.')
        kind = 'review' if op['candidate_version'] == 0 else 'review_revision'
    reservation = reserve_quality_call(conn, learner_id, operation_id, kind)
    call_id = reservation['call_id']
    if not reservation['reserved']:
        # Deadline expiry may have failed the operation before making a call.
        with conn:
            current = _operation(conn, learner_id, operation_id)
            if current['stage'] == 'failed':
                _fail_preparing_session(conn, current, current['failure_code'])
        return _result(conn, learner_id, operation_id, call_id=call_id)
    try:
        context = build_private_reviewer_context(conn, learner_id, call_id)
    except QualityStateRejected:
        _fail_quality_call(conn, learner_id, operation_id, call_id, 'review_context_invalid')
        return _result(conn, learner_id, operation_id, call_id=call_id)
    # Recompute after context loading; never reuse the reservation's older allowance.
    remaining = reservation['operation']['deadline_at'] - time.time()
    if remaining <= 1:
        _fail_quality_call(conn, learner_id, operation_id, call_id, 'quality_deadline')
        return _result(conn, learner_id, operation_id, call_id=call_id)
    try:
        report = reviewer(copy.deepcopy(context), provider_timeout_ms=min(60_000, int(remaining * 1000) - 1000),
                          process_timeout_seconds=min(70, remaining))
    except Exception as error:
        code = 'review_' + ReviewerFailure(code=error.code).code if isinstance(error, ReviewerFailure) else 'review_adapter_error'
        _fail_quality_call(conn, learner_id, operation_id, call_id, code)
        return _result(conn, learner_id, operation_id, dispatched=True, call_id=call_id)
    try:
        # Decision/report/call completion are atomic; no model verdict is trusted.
        stored = store_review_report(conn, learner_id, call_id, report)
    except (ReviewRejected, QualityStateRejected, ValueError, TypeError):
        _fail_quality_call(conn, learner_id, operation_id, call_id, 'review_invalid_report')
    except sqlite3.Error:
        _fail_quality_call(conn, learner_id, operation_id, call_id, 'review_save_failed')
    else:
        if stored['operation']['stage'] == 'failed':
            with conn:
                _fail_preparing_session(conn, stored['operation'], stored['operation']['failure_code'])
    return _result(conn, learner_id, operation_id, dispatched=True, call_id=call_id)


def dispatch_quality_revision(conn, learner_id, operation_id, *, assessment=None, reviewer=run_reviewer):
    """One content revision, at most one remaining technical repair, full review.

    Pending reserved generator calls are never resent after duplicate entry or
    crash. An already saved replacement may proceed to its once-only review.
    """
    from study_buddy.assessment import run_assessment_revision, AssessmentFailure
    from study_buddy.quality_state import complete_quality_call
    from study_buddy.quality_storage import build_private_revision_context, store_candidate, _handoff
    from scripts.validate_quiz import check as check_quiz
    assessment = assessment or run_assessment_revision
    with conn:
        op = _operation(conn, learner_id, operation_id)
    if op['stage'] in ('approved', 'failed'):
        if op['stage'] == 'failed':
            with conn: _fail_preparing_session(conn, op, op['failure_code'])
        return _result(conn, learner_id, operation_id)
    if op['stage'] == 'reviewing_revision':
        return dispatch_quality_review(conn, learner_id, operation_id, reviewer=reviewer)
    if op['stage'] == 'revising':
        return _result(conn, learner_id, operation_id)
    if op['stage'] not in ('needs_revision', 'revision_invalid'):
        raise QualityStateRejected('A saved content-failure report is required.')
    kind = 'revision' if op['stage'] == 'needs_revision' else 'technical_repair'
    attempted = False
    for _ in range(2):
        grant = reserve_quality_call(conn, learner_id, operation_id, kind)
        call_id = grant['call_id']
        if not grant['reserved']:
            with conn:
                current = _operation(conn, learner_id, operation_id)
                if current['stage'] == 'failed': _fail_preparing_session(conn, current, current['failure_code'])
            return _result(conn, learner_id, operation_id, dispatched=attempted, call_id=call_id)
        try:
            context = build_private_revision_context(conn, learner_id, call_id)
        except QualityStateRejected:
            _fail_quality_call(conn, learner_id, operation_id, call_id, 'revision_context_invalid')
            return _result(conn, learner_id, operation_id, dispatched=attempted, call_id=call_id)
        remaining = grant['operation']['deadline_at'] - time.time()
        if remaining <= 1:
            _fail_quality_call(conn, learner_id, operation_id, call_id, 'quality_deadline')
            return _result(conn, learner_id, operation_id, dispatched=attempted, call_id=call_id)
        attempted = True
        try:
            output = assessment(copy.deepcopy(context), technical_repair=kind=='technical_repair',
                                provider_timeout_ms=min(60_000, int(remaining*1000)-1000),
                                process_timeout_seconds=min(70, remaining))
        except Exception as error:
            code = error.code if isinstance(error, AssessmentFailure) and isinstance(error.code, str) else 'adapter_error'
            safe = AssessmentFailure(code=code).code
            _fail_quality_call(conn, learner_id, operation_id, call_id, 'revision_' + safe)
            return _result(conn, learner_id, operation_id, dispatched=True, call_id=call_id)
        original = context['original']
        header, teaching = original['controller_context'], original['teaching']
        handoff = _handoff(header, teaching['explanation_text'], teaching['concept_ids'])
        try:
            valid = not check_quiz(output, handoff)
        except Exception:
            valid = False
        try:
            saved = (store_candidate(conn, learner_id, call_id, output) if valid else
                     complete_quality_call(conn, learner_id, call_id, outcome='invalid'))
        except (QualityStateRejected, ValueError, TypeError):
            _fail_quality_call(conn, learner_id, operation_id, call_id, 'revision_invalid_candidate')
            return _result(conn, learner_id, operation_id, dispatched=True, call_id=call_id)
        except sqlite3.Error:
            _fail_quality_call(conn, learner_id, operation_id, call_id, 'revision_save_failed')
            return _result(conn, learner_id, operation_id, dispatched=True, call_id=call_id)
        if saved['operation']['stage'] == 'failed':
            with conn: _fail_preparing_session(conn, saved['operation'], saved['operation']['failure_code'])
            return _result(conn, learner_id, operation_id, dispatched=True, call_id=call_id)
        if valid:
            return dispatch_quality_review(conn, learner_id, operation_id, reviewer=reviewer)
        kind = 'technical_repair'
    return _result(conn, learner_id, operation_id, dispatched=attempted)
