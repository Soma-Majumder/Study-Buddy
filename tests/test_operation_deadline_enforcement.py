"""Deadline boundaries and late-output fencing without agents or sleeping."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests import test_feedback as feedback_fixtures
from tests import test_reteaching as reteach_fixtures
from study_buddy.feedback import explain_results
from study_buddy.reteaching import dispatch_reteaching
from study_buddy.operation_deadlines import check_operation_deadline_locked

LEARNER = feedback_fixtures.LEARNER


def forbidden(*args, **kwargs):
    raise AssertionError('Expired/replayed work dispatched an agent')


def crash(*args, **kwargs):
    raise KeyboardInterrupt('Simulated loss')


class OperationDeadlineEnforcementTests(unittest.TestCase):
    def feedback(self):
        h = feedback_fixtures.FeedbackTests(); h.setUp(); self.addCleanup(h.doCleanups)
        h.original_feedback = h.conn.execute('SELECT feedback FROM results').fetchone()[0]
        return h

    def reteaching(self):
        h = reteach_fixtures.ReteachingTests(); h.setUp(); self.addCleanup(h.doCleanups)
        return h

    def test_feedback_boundary_and_idempotent_replay_preserve_score_and_budget(self):
        h = self.feedback()
        with self.assertRaises(KeyboardInterrupt): h.explain(crash)
        deadline = h.conn.execute('SELECT deadline_at FROM feedback_operation_deadlines').fetchone()[0]
        with patch('study_buddy.operation_deadlines.time.time', return_value=deadline-.001):
            self.assertEqual(h.explain(forbidden)['operation_status'], 'running')
        with patch('study_buddy.operation_deadlines.time.time', return_value=deadline):
            out = h.explain(forbidden)
        self.assertEqual((out['operation_status'],out['status']), ('failed','failed'))
        self.assertEqual(h.conn.execute('SELECT failure_reason,learning_retry_count FROM study_sessions').fetchone(), ('feedback_deadline',0))
        self.assertEqual(h.conn.execute('SELECT score,feedback FROM results').fetchone(), (6,h.original_feedback))
        before = list(h.conn.iterdump())
        h.explain(forbidden)
        self.assertEqual(before, list(h.conn.iterdump()))

    def test_late_valid_feedback_is_discarded(self):
        h = self.feedback()
        clock = [1000]
        def late(context, **kwargs):
            self.assertFalse(h.conn.in_transaction)
            out = h.feedback(context, **kwargs)
            clock[0] = 1180
            return out
        with patch('study_buddy.operation_deadlines.time.time', side_effect=lambda: clock[0]):
            out = h.explain(late)
        self.assertEqual(out['operation_status'], 'failed')
        self.assertEqual(h.conn.execute('SELECT feedback FROM results').fetchone()[0], h.original_feedback)
        self.assertEqual(len(h.calls), 1)

    def test_expired_invalid_feedback_does_not_reserve_repair(self):
        h = self.feedback()
        clock = [1000]
        def late(context, **kwargs):
            clock[0] = 1180
            return {}
        with patch('study_buddy.operation_deadlines.time.time', side_effect=lambda: clock[0]):
            out = h.explain(late)
        self.assertEqual(out['operation_status'], 'failed')
        self.assertEqual(h.conn.execute('SELECT SUM(repair_count) FROM handoff_repairs').fetchone()[0], 0)

    def test_expiry_during_repair_preserves_consumed_budget(self):
        h = self.feedback()
        clock = [1000]
        def repair(context, **kwargs):
            if kwargs['repair_request'] is None:
                return {}
            clock[0] = 1180
            return h.feedback(context, **kwargs)
        with patch('study_buddy.operation_deadlines.time.time', side_effect=lambda: clock[0]):
            out = h.explain(repair)
        self.assertEqual(out['operation_status'], 'failed')
        self.assertEqual(h.conn.execute('SELECT SUM(repair_count) FROM handoff_repairs').fetchone()[0], 1)
        self.assertEqual(h.conn.execute('SELECT score FROM results').fetchone()[0], 6)

    def test_feedback_deadline_before_dispatch_prevents_call(self):
        from study_buddy.operation_deadlines import record_operation_deadline
        h = self.feedback()
        clock = [1000]
        def reserve(*args):
            record_operation_deadline(*args)
            clock[0] = 1180
        with patch('study_buddy.operation_deadlines.time.time', side_effect=lambda: clock[0]), \
             patch('study_buddy.feedback.record_operation_deadline', side_effect=reserve):
            out = h.explain(forbidden)
        self.assertEqual(out['operation_status'], 'failed')

    def test_reserved_reteaching_expires_before_tutor_at_exact_boundary(self):
        h = self.reteaching()
        deadline = h.conn.execute('SELECT deadline_at FROM reteaching_operation_deadlines').fetchone()[0]
        with patch('study_buddy.operation_deadlines.time.time', return_value=deadline):
            out = h.dispatch(forbidden)
        self.assertEqual((out['operation_status'],out['status']), ('failed','failed'))
        self.assertIsNone(out['handoff'])
        self.assertEqual(h.conn.execute('SELECT failure_reason,learning_retry_count FROM study_sessions').fetchone(), ('reteaching_deadline',1))
        self.assertEqual(h.conn.execute('SELECT COUNT(*) FROM explanations WHERE round=\'retry\'').fetchone()[0], 0)

    def test_late_tutor_success_and_exception_cannot_revive_expired_route(self):
        for error in (False,True):
            with self.subTest(error=error):
                h = self.reteaching()
                deadline = h.conn.execute('SELECT deadline_at FROM reteaching_operation_deadlines').fetchone()[0]
                clock = [deadline-1]
                def tutor(context):
                    self.assertFalse(h.conn.in_transaction)
                    out = h.tutor(context)
                    clock[0] = deadline
                    if error: raise RuntimeError('late provider error')
                    return out
                with patch('study_buddy.operation_deadlines.time.time', side_effect=lambda: clock[0]):
                    out = h.dispatch(tutor)
                self.assertEqual(out['operation_status'], 'failed')
                self.assertIsNone(out['handoff'])
                self.assertEqual(h.conn.execute("SELECT COUNT(*) FROM explanations WHERE round='retry'").fetchone()[0], 0)
                self.assertEqual(h.conn.execute('SELECT failure_reason FROM study_sessions').fetchone()[0], 'reteaching_deadline')
                self.assertEqual(h.dispatch(forbidden)['operation_status'], 'failed')

    def test_completed_feedback_and_saved_reteaching_are_not_expired(self):
        f = self.feedback(); f.explain()
        r = self.reteaching(); r.dispatch()
        for h, table, action in ((f,'feedback_operation_deadlines',f.explain), (r,'reteaching_operation_deadlines',r.dispatch)):
            deadline = h.conn.execute(f'SELECT deadline_at FROM {table}').fetchone()[0]
            before = list(h.conn.iterdump())
            with patch('study_buddy.operation_deadlines.time.time', return_value=deadline+10000):
                self.assertEqual(action(forbidden)['operation_status'], 'ready')
            self.assertEqual(before, list(h.conn.iterdump()))

    def test_missing_timing_blocks_dispatch_without_guessing_a_new_deadline(self):
        h = self.reteaching()
        h.conn.execute('DELETE FROM reteaching_operation_deadlines'); h.conn.commit()
        with self.assertRaises(ValueError): h.dispatch(forbidden)
        self.assertEqual(h.conn.execute('SELECT status FROM session_routes').fetchone()[0], 'reserved')

    def test_concurrent_feedback_expiry_discards_winners_late_result(self):
        h = self.feedback()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'late-feedback.db'
            with closing(sqlite3.connect(path)) as disk: h.conn.backup(disk)
            entered, release = threading.Event(), threading.Event()
            def winner():
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON')
                    def callback(context, **kwargs):
                        self.assertFalse(conn.in_transaction)
                        entered.set()
                        if not release.wait(10): raise AssertionError('Callback not released')
                        return h.feedback(context, **kwargs)
                    return explain_results(conn, LEARNER, h.attempt_id, assessment=callback)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(winner)
                try:
                    self.assertTrue(entered.wait(5))
                    with closing(sqlite3.connect(path)) as conn:
                        with conn:
                            conn.execute('UPDATE study_sessions SET status=status')
                            deadline = conn.execute('SELECT deadline_at FROM feedback_operation_deadlines').fetchone()[0]
                            self.assertFalse(check_operation_deadline_locked(conn,'feedback',LEARNER,h.attempt_id,now=deadline))
                    self.assertFalse(future.done())
                finally: release.set()
                self.assertEqual(future.result(timeout=5)['operation_status'], 'failed')
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT feedback FROM results').fetchone()[0], h.original_feedback)
                self.assertEqual(conn.execute('SELECT failure_reason FROM study_sessions').fetchone()[0], 'feedback_deadline')

    def test_expiry_recording_failure_rolls_back_operation_and_session_together(self):
        h = self.feedback()
        with self.assertRaises(KeyboardInterrupt): h.explain(crash)
        h.conn.execute("CREATE TRIGGER fail_expiry BEFORE UPDATE ON study_sessions WHEN NEW.status='failed' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        h.conn.commit()
        before = list(h.conn.iterdump())
        deadline = h.conn.execute('SELECT deadline_at FROM feedback_operation_deadlines').fetchone()[0]
        with patch('study_buddy.operation_deadlines.time.time', return_value=deadline):
            with self.assertRaises(sqlite3.IntegrityError): h.explain(forbidden)
        self.assertEqual(before, list(h.conn.iterdump()))

    def test_late_feedback_exception_keeps_deadline_failure_reason(self):
        h = self.feedback()
        clock = [1000]
        def late(context, **kwargs):
            clock[0] = 1180
            raise RuntimeError('late provider exception')
        with patch('study_buddy.operation_deadlines.time.time', side_effect=lambda: clock[0]):
            self.assertEqual(h.explain(late)['operation_status'], 'failed')
        self.assertEqual(h.conn.execute('SELECT failure_reason FROM study_sessions').fetchone()[0], 'feedback_deadline')

    def test_concurrent_reteaching_expiry_discards_late_handoff(self):
        h = self.reteaching()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'late-tutor.db'
            with closing(sqlite3.connect(path)) as disk: h.conn.backup(disk)
            entered, release = threading.Event(), threading.Event()
            def winner():
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON')
                    def tutor(context):
                        self.assertFalse(conn.in_transaction)
                        entered.set()
                        if not release.wait(10): raise AssertionError('Tutor not released')
                        return h.tutor(context)
                    return dispatch_reteaching(conn, LEARNER, h.attempt_id, tutor=tutor)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(winner)
                try:
                    self.assertTrue(entered.wait(5))
                    with closing(sqlite3.connect(path)) as conn:
                        with conn:
                            conn.execute('UPDATE study_sessions SET status=status')
                            deadline = conn.execute('SELECT deadline_at FROM reteaching_operation_deadlines').fetchone()[0]
                            self.assertFalse(check_operation_deadline_locked(conn,'reteaching',LEARNER,h.attempt_id,now=deadline))
                    self.assertFalse(future.done())
                finally: release.set()
                result = future.result(timeout=5)
                self.assertEqual(result['operation_status'], 'failed')
                self.assertIsNone(result['handoff'])
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM explanations WHERE round='retry'").fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT failure_reason,learning_retry_count FROM study_sessions').fetchone(), ('reteaching_deadline',1))
