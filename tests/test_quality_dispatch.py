"""Controller Review dispatch: durable once-only grants and fail-closed saves."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from tests import test_quality_storage as fixtures
from study_buddy.controller import dispatch_quality_review
from study_buddy.quality_state import QualityStateRejected, reserve_quality_call
from study_buddy.quality_storage import store_candidate
from study_buddy.reviewer import ReviewerFailure

LEARNER = fixtures.LEARNER


class QualityDispatchTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.QualityStorageTests(); self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn, self.op = self.helper.conn, self.helper.op
        self.helper.stage()
        self.calls = []

    def reviewer(self, context, **timeouts):
        self.calls.append((copy.deepcopy(context), timeouts))
        self.assertFalse(self.conn.in_transaction)
        return self.helper.report()

    def dispatch(self, **kwargs):
        return dispatch_quality_review(self.conn, LEARNER, self.op, reviewer=kwargs.get('reviewer', self.reviewer))

    def test_pass_is_saved_once_without_publication_or_private_response(self):
        result = self.dispatch()
        self.assertEqual((result['stage'], result['next_step']), ('approved', 'publish_reviewed_quiz'))
        self.assertTrue(result['dispatched']); self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'teaching')
        for private in ('answer_key', 'correct_option', 'rationale', 'support_quotes', 'explanation_text'):
            self.assertNotIn(private, json.dumps(result))
        self.assertFalse(self.dispatch()['dispatched']); self.assertEqual(len(self.calls), 1)

    def test_content_failure_requests_revision_without_automatic_extra_call(self):
        def reviewer(context, **kwargs):
            r = self.reviewer(context, **kwargs)
            r['questions'][0]['plausible_distractors'] = 'fail'
            r['questions'][0]['issues'] = [dict(code='weak_distractor', detail='Implausible.', related_positions=[])]
            return r
        result = self.dispatch(reviewer=reviewer)
        self.assertEqual((result['stage'], result['next_step']), ('needs_revision', 'revise_quiz'))
        self.assertFalse(self.dispatch()['dispatched']); self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)

    def test_uncertainty_marks_operation_and_session_failed(self):
        def reviewer(context, **kwargs):
            r = self.reviewer(context, **kwargs)
            r['questions'][0]['grounding'] = 'uncertain'
            r['questions'][0]['uncertainty_notes'] = ['Cannot verify.']
            return r
        result = self.dispatch(reviewer=reviewer)
        self.assertEqual(result['error'], 'review_uncertainty')
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'failed')
        self.assertFalse(self.dispatch()['dispatched'])

    def test_provider_and_unexpected_errors_are_safe_terminal_failures(self):
        def reviewer(_context, **kwargs): raise ReviewerFailure(code='request_timeout')
        result = self.dispatch(reviewer=reviewer)
        self.assertEqual((result['stage'], result['error']), ('failed', 'review_request_timeout'))
        self.assertEqual(self.conn.execute("SELECT status, outcome FROM quiz_quality_calls WHERE kind='review'").fetchone(), ('failed', 'error'))
        self.assertFalse(self.dispatch()['dispatched'])

    def test_arbitrary_callback_exception_is_not_saved(self):
        def reviewer(_context, **kwargs): raise RuntimeError('private body and key')
        result = self.dispatch(reviewer=reviewer)
        self.assertEqual(result['error'], 'review_adapter_error')
        self.assertNotIn('private body', '\n'.join(self.conn.iterdump()))

    def test_malformed_stale_and_invented_evidence_fail_without_review_repair(self):
        def reviewer(context, **kwargs):
            r = self.reviewer(context, **kwargs)
            r['questions'][0]['support_quotes'] = ['invented evidence']
            return r
        result = self.dispatch(reviewer=reviewer)
        self.assertEqual((result['stage'], result['error']), ('failed', 'review_invalid_report'))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT review_count FROM quiz_quality_operations').fetchone()[0], 1)

    def test_preexisting_reserved_call_is_not_restarted(self):
        call = reserve_quality_call(self.conn, LEARNER, self.op, 'review')
        result = self.dispatch()
        self.assertEqual(result['call_id'], call['call_id']); self.assertFalse(result['dispatched'])
        self.assertEqual(self.calls, [])
        self.assertEqual(result['stage'], 'reviewing')

    def test_expiry_before_reservation_makes_no_call(self):
        self.conn.execute('UPDATE quiz_quality_operations SET started_at=?, deadline_at=?', (time.time()-181, time.time()-1))
        self.conn.commit()
        result = self.dispatch()
        self.assertEqual(result['error'], 'quality_deadline'); self.assertEqual(self.calls, [])
        self.assertEqual(self.conn.execute('SELECT review_count FROM quiz_quality_operations').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'failed')

    def test_deadline_recomputed_after_loading_and_late_results_not_saved(self):
        def reviewer(context, **kwargs):
            self.assertLessEqual(kwargs['provider_timeout_ms'], 20_000)
            self.conn.execute('UPDATE quiz_quality_operations SET started_at=?, deadline_at=?', (time.time()-181, time.time()-1))
            self.conn.commit()
            return self.helper.report()
        self.conn.execute('UPDATE quiz_quality_operations SET deadline_at=?', (time.time()+20,)); self.conn.commit()
        result = self.dispatch(reviewer=reviewer)
        self.assertEqual(result['error'], 'quality_deadline')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 0)

    def test_invalid_saved_context_stops_before_provider(self):
        self.conn.execute("UPDATE explanations SET text=text || 'changed'"); self.conn.commit()
        result = self.dispatch()
        self.assertEqual(result['error'], 'review_context_invalid'); self.assertEqual(self.calls, [])

    def test_report_save_failure_rolls_back_then_records_terminal_failure(self):
        self.conn.execute("CREATE TRIGGER break_review_save BEFORE INSERT ON quiz_quality_reports BEGIN SELECT RAISE(ABORT, 'private save error'); END")
        self.conn.commit()
        result = self.dispatch()
        self.assertEqual(result['error'], 'review_save_failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT status FROM quiz_quality_calls WHERE kind='review'").fetchone()[0], 'failed')

    def test_failure_recording_failure_propagates_instead_of_claiming_success(self):
        self.conn.execute("CREATE TRIGGER break_failure BEFORE UPDATE ON quiz_quality_operations WHEN NEW.stage='failed' BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        self.conn.commit()
        def reviewer(_context, **kwargs): raise ReviewerFailure(code='transport_error')
        with self.assertRaises(sqlite3.Error): self.dispatch(reviewer=reviewer)
        self.assertEqual(self.conn.execute("SELECT status FROM quiz_quality_calls WHERE kind='review'").fetchone()[0], 'reserved')
        self.assertFalse(self.dispatch()['dispatched'])

    def test_wrong_owner_rejected_without_reservation(self):
        with self.assertRaises(QualityStateRejected):
            dispatch_quality_review(self.conn, 'learner_other', self.op, reviewer=self.reviewer)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.conn.execute('SELECT review_count FROM quiz_quality_operations').fetchone()[0], 0)

    def test_revision_candidate_uses_second_review_and_rejects_second_content_failure(self):
        def bad(context, **kwargs):
            r = self.helper.report(context['controller_context']['candidate_version'])
            r['quiz_review']['target_diversity'] = 'fail'
            r['quiz_review']['issues'] = [dict(code='repeated_target', detail='Repeated.', related_positions=[1, 2])]
            return r
        self.dispatch(reviewer=bad)
        revision = reserve_quality_call(self.conn, LEARNER, self.op, 'revision')
        store_candidate(self.conn, LEARNER, revision['call_id'], self.helper.candidate)
        result = self.dispatch(reviewer=bad)
        self.assertEqual(result['error'], 'content_revision_exhausted')
        self.assertEqual(self.conn.execute('SELECT review_count FROM quiz_quality_operations').fetchone()[0], 2)
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'failed')
        self.assertFalse(self.dispatch()['dispatched'])

    def test_concurrent_requests_dispatch_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.db'
            with closing(sqlite3.connect(path)) as target: self.conn.backup(target)
            entered, release = threading.Event(), threading.Event()
            calls = []
            def reviewer(_context, **kwargs):
                calls.append(1); entered.set()
                if not release.wait(5): raise RuntimeError('test synchronization failed')
                return self.helper.report()
            def dispatch():
                with closing(sqlite3.connect(path, timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON')
                    return dispatch_quality_review(conn, LEARNER, self.op, reviewer=reviewer)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(dispatch)
                try:
                    self.assertTrue(entered.wait(5))
                    second = pool.submit(dispatch).result(timeout=5)
                    self.assertFalse(second['dispatched'])
                finally: release.set()
                self.assertTrue(first.result(timeout=5)['dispatched'])
            self.assertEqual(calls, [1])

    def test_stale_session_after_provider_is_not_overwritten(self):
        def reviewer(context, **kwargs):
            self.conn.execute("UPDATE study_sessions SET status='pending_save'"); self.conn.commit()
            return self.helper.report()
        result = self.dispatch(reviewer=reviewer)
        self.assertEqual(result['stage'], 'failed')
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'pending_save')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 0)

    def test_retry_round_failure_preserves_consumed_learning_retry(self):
        from tests import test_retry_quiz as retry_fixtures
        from study_buddy.quality_state import start_quality_operation
        helper = retry_fixtures.RetryQuizTests(); helper.setUp()
        self.addCleanup(helper.doCleanups)
        conn = helper.conn
        sid, data = conn.execute("SELECT session_id, context FROM session_routes WHERE route='reteach'").fetchone()
        h = json.loads(data)['tutor_handoff']
        op = start_quality_operation(conn, LEARNER, sid, h['handoff_id'], h['payload']['explanation_id'], 'retry')['operation']['operation_id']
        call = reserve_quality_call(conn, LEARNER, op, 'generation')
        store_candidate(conn, LEARNER, call['call_id'], helper.assessment(h))
        def reviewer(context, **kwargs):
            self.assertEqual(context['controller_context']['round'], 'retry')
            self.assertEqual(set(context['teaching']['focus_concept_ids']), set(h['payload']['focus_concept_ids']))
            raise ReviewerFailure(code='output_truncated')
        result = dispatch_quality_review(conn, LEARNER, op, reviewer=reviewer)
        self.assertEqual(result['error'], 'review_output_truncated')
        self.assertEqual(conn.execute('SELECT status, learning_retry_count FROM study_sessions').fetchone(), ('failed', 1))
