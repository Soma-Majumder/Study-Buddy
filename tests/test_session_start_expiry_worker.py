"""Maintenance expires abandoned claims without agents or claim release."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from tests import test_controller as fixtures
from tests.quality_fixtures import passing_review
from study_buddy.controller import start_quiz_session, start_session
from study_buddy.maintenance_worker import run_maintenance_cycle
from study_buddy.quality_state import start_quality_operation
from study_buddy.session_start_requests import reserve_session_start, sweep_expired_session_starts, StartRequestRejected

LEARNER = fixtures.LEARNER
LESSON = 'lesson_cell_membranes'


class SessionStartExpiryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.ControllerTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn = self.helper.conn

    def reserve(self, request='expiry_worker_01', old=True):
        claim = reserve_session_start(self.conn, LEARNER, request, LESSON, 2)
        if old:
            self.conn.execute("UPDATE session_start_requests SET created_at='2000-01-01T00:00:00.000Z' WHERE request_id=?", (request,))
            self.conn.commit()
        return claim

    def test_bounded_batches_and_repeated_sweep_preserve_claims_and_budgets(self):
        self.reserve(); self.reserve('expiry_worker_02'); self.reserve('expiry_worker_03', old=False)
        for _ in range(2):
            self.assertEqual(sweep_expired_session_starts(self.conn, limit=1),
                             dict(session_starts_checked=1, session_starts_expired=1))
        before = list(self.conn.iterdump())
        self.assertEqual(sweep_expired_session_starts(self.conn, limit=1)['session_starts_checked'], 0)
        self.assertEqual(before, list(self.conn.iterdump()))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM session_start_requests').fetchone()[0], 3)
        self.assertEqual(self.conn.execute('SELECT SUM(repair_count) FROM handoff_repairs').fetchone()[0], 0)
        self.assertEqual(self.helper.calls, [])
        replay = reserve_session_start(self.conn, LEARNER, 'expiry_worker_01', LESSON, 2)
        self.assertFalse(replay['reserved'])
        self.assertEqual(replay['status'], 'failed')

    def test_exact_deadline_boundary(self):
        self.reserve()
        deadline = datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp() + 120
        self.assertEqual(sweep_expired_session_starts(self.conn, now=deadline-.001)['session_starts_expired'], 0)
        self.assertEqual(sweep_expired_session_starts(self.conn, now=deadline)['session_starts_expired'], 1)

    def test_quality_operation_owns_expiry_after_tutor(self):
        started = start_session(self.conn, LEARNER, LESSON, tutor=self.helper.tutor,
                                request_id='expiry_worker_01', lesson_version=2)
        h = started['handoff']
        start_quality_operation(self.conn, LEARNER, started['session_id'], h['handoff_id'], h['payload']['explanation_id'], 'first')
        self.conn.execute("UPDATE session_start_requests SET created_at='2000-01-01T00:00:00.000Z'")
        self.conn.commit()
        before = list(self.conn.iterdump())
        self.assertEqual(sweep_expired_session_starts(self.conn)['session_starts_checked'], 0)
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_published_quiz_and_terminal_sessions_are_preserved(self):
        self.helper.assessment_calls = []
        ready = start_quiz_session(self.conn, LEARNER, LESSON, tutor=self.helper.tutor,
            assessment=self.helper.assessment, reviewer=passing_review,
            request_id='expiry_worker_01', lesson_version=2)
        self.assertEqual(ready['status'], 'awaiting_answers')
        claim = self.reserve('expiry_worker_02')
        self.conn.execute("UPDATE study_sessions SET status='failed',failure_reason='existing_failure' WHERE session_id=?", (claim['session_id'],))
        self.conn.execute("UPDATE session_start_requests SET created_at='2000-01-01T00:00:00.000Z'")
        self.conn.commit()
        before = list(self.conn.iterdump())
        self.assertEqual(sweep_expired_session_starts(self.conn)['session_starts_checked'], 0)
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_batch_failure_rolls_back_all_start_expirations(self):
        self.reserve(); self.reserve('expiry_worker_02')
        self.conn.execute("CREATE TRIGGER fail_second BEFORE UPDATE ON study_sessions WHEN NEW.status='failed' AND (SELECT COUNT(*) FROM study_sessions WHERE status='failed')=1 BEGIN SELECT RAISE(ABORT,'fixture'); END")
        self.conn.commit()
        before = list(self.conn.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            sweep_expired_session_starts(self.conn)
        self.assertEqual(before, list(self.conn.iterdump()))
        self.assertFalse(self.conn.in_transaction)

    def test_invalid_arguments_and_pending_caller_work_are_rejected(self):
        for limit in (0, 1001, True):
            with self.assertRaises(StartRequestRejected):
                sweep_expired_session_starts(self.conn, limit=limit)
        with self.assertRaises(StartRequestRejected):
            sweep_expired_session_starts(self.conn, now=float('nan'))
        self.conn.execute('UPDATE learners SET learner_id=learner_id')
        with self.assertRaises(StartRequestRejected):
            sweep_expired_session_starts(self.conn)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()

    def test_worker_expires_inflight_start_and_late_tutor_cannot_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'worker.db'
            with closing(sqlite3.connect(path)) as target:
                self.conn.backup(target)
            entered, release = threading.Event(), threading.Event()
            def forbidden(*args, **kwargs):
                self.fail('Expired start dispatched downstream work')
            def winner():
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON')
                    def tutor(context):
                        result = self.helper.tutor(context)
                        self.assertFalse(conn.in_transaction)
                        entered.set()
                        if not release.wait(10):
                            raise AssertionError('Tutor not released')
                        return result
                    return start_quiz_session(conn, LEARNER, LESSON, tutor=tutor,
                        assessment=forbidden, reviewer=forbidden, request_id='expiry_worker_01', lesson_version=2)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(winner)
                try:
                    self.assertTrue(entered.wait(5))
                    with closing(sqlite3.connect(path)) as conn:
                        conn.execute("UPDATE session_start_requests SET created_at='2000-01-01T00:00:00.000Z'")
                        conn.commit()
                    result = run_maintenance_cycle(path, limit=1)
                    self.assertEqual(result['session_starts_expired'], 1)
                    self.assertEqual(result['sessions_failed'], 1)
                    self.assertFalse(future.done())
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=5)['status'], 'failed')
            self.assertTrue(all(v == 0 for v in run_maintenance_cycle(path).values()))
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('PRAGMA foreign_keys=ON')
                replay = start_quiz_session(conn, LEARNER, LESSON, tutor=forbidden,
                    assessment=forbidden, reviewer=forbidden, request_id='expiry_worker_01', lesson_version=2)
                self.assertFalse(replay['reserved'])
                self.assertEqual(replay['status'], 'failed')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM explanations').fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT failure_reason FROM study_sessions').fetchone()[0], 'session_start_deadline')
            self.assertEqual(len(self.helper.calls), 1)
