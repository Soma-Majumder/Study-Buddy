"""Fixed reservation deadlines reject late Tutor output without another dispatch."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from tests import test_controller as fixtures
from study_buddy.controller import start_quiz_session
from study_buddy.session_start_requests import (reserve_session_start,
    check_session_start_deadline_locked, SESSION_START_WINDOW_SECONDS)

LEARNER = fixtures.LEARNER
OPTIONS = dict(request_id='deadline_start_01', lesson_version=2)


class SessionStartDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.ControllerTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn = self.helper.conn

    def reserve(self):
        return reserve_session_start(self.conn, LEARNER, OPTIONS['request_id'], 'lesson_cell_membranes', 2)

    def test_boundary_expires_once_and_replay_never_refreshes_claim(self):
        claim = self.reserve()
        deadline = datetime.strptime(claim['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc).timestamp() + SESSION_START_WINDOW_SECONDS
        with self.conn:
            self.conn.execute('UPDATE study_sessions SET status=status')
            self.assertTrue(check_session_start_deadline_locked(self.conn, LEARNER, claim['session_id'], now=deadline-.001))
            self.assertFalse(check_session_start_deadline_locked(self.conn, LEARNER, claim['session_id'], now=deadline))
        self.assertEqual(self.conn.execute('SELECT status,failure_reason FROM study_sessions').fetchone(), ('failed', 'session_start_deadline'))
        before = list(self.conn.iterdump())
        replay = self.reserve()
        self.assertFalse(replay['reserved'])
        self.assertEqual(replay['created_at'], claim['created_at'])
        self.assertEqual(replay['status'], 'failed')
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_old_unvisited_claim_expires_on_replay_without_tutor(self):
        self.reserve()
        self.conn.execute("UPDATE session_start_requests SET created_at='2000-01-01T00:00:00.000Z'")
        self.conn.commit()
        result = self.reserve()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.helper.calls, [])

    def test_late_valid_tutor_result_is_not_saved_or_sent_to_assessment(self):
        def tutor(context):
            result = self.helper.tutor(context)
            # Simulate the elapsed wall time while no controller transaction is open.
            self.assertFalse(self.conn.in_transaction)
            self.conn.execute("UPDATE session_start_requests SET created_at='2000-01-01T00:00:00.000Z'")
            self.conn.commit()
            return result
        def forbidden(*args, **kwargs):
            self.fail('Late Tutor result reached Assessment/Reviewer')
        result = start_quiz_session(self.conn, LEARNER, 'lesson_cell_membranes',
            tutor=tutor, assessment=forbidden, reviewer=forbidden, **OPTIONS)
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['handoff'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM explanations').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_operations').fetchone()[0], 0)
        replay = start_quiz_session(self.conn, LEARNER, 'lesson_cell_membranes',
            tutor=forbidden, assessment=forbidden, reviewer=forbidden, **OPTIONS)
        self.assertFalse(replay['reserved'])
        self.assertEqual(len(self.helper.calls), 1)

    def test_terminal_session_cannot_accept_late_result(self):
        def tutor(context):
            result = self.helper.tutor(context)
            self.conn.execute("UPDATE study_sessions SET status='failed',failure_reason='session_start_deadline'")
            self.conn.commit()
            return result
        result = start_quiz_session(self.conn, LEARNER, 'lesson_cell_membranes', tutor=tutor, **OPTIONS)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM explanations').fetchone()[0], 0)

    def test_expiry_before_dispatch_calls_no_tutor(self):
        original = reserve_session_start
        def delayed(*args):
            claim = original(*args)
            self.conn.execute("UPDATE session_start_requests SET created_at='2000-01-01T00:00:00.000Z'")
            self.conn.commit()
            return claim
        with patch('study_buddy.controller.reserve_session_start', side_effect=delayed):
            result = start_quiz_session(self.conn, LEARNER, 'lesson_cell_membranes', tutor=self.helper.tutor, **OPTIONS)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.helper.calls, [])
