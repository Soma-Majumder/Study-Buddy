"""Reserved retry dispatch: actual SQLite routes, fixture Tutor, no API calls."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from tests import test_routing as fixtures
from study_buddy.controller import dispatch_reteaching, decide_next_step, TutorFailure
from study_buddy.reteaching import ReteachingRejected

ROOT = Path(__file__).resolve().parent.parent
LEARNER = fixtures.LEARNER


class ReteachingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RoutingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.conn = self.fixture.conn
        self.source = self.fixture.prepare()
        self.attempt_id = self.source['attempt_id']
        self.route = decide_next_step(self.conn, LEARNER, self.attempt_id)
        self.calls = []

    def tutor(self, context):
        self.calls.append(copy.deepcopy(context))
        h = json.loads((ROOT / 'schemas/v1/examples/tutor_to_assessment.retry.example.json').read_text())
        h['handoff_id'] = context['handoffId']
        h['context']['learner_session_id'] = context['sessionId']
        h['payload']['explanation_id'] = context['explanationId']
        return dict(handoff=h, repairCount=0)

    def dispatch(self, tutor=None):
        return dispatch_reteaching(self.conn, LEARNER, self.attempt_id, tutor=tutor or self.tutor)

    def test_claimed_retry_reaches_tutor_with_actual_count_and_saved_focus(self):
        result = self.dispatch()
        self.assertEqual((result['operation_status'], result['next_step']), ('ready', 'assessment'))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['learningRetryCount'], 1)
        self.assertEqual(self.calls[0]['retryReservation']['status'], 'dispatched')
        self.assertEqual(self.calls[0]['retryReservation']['sourceAttemptId'], self.attempt_id)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM explanations WHERE round = 'retry'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)
        self.assertEqual(set(result['handoff']['payload']['focus_concept_ids']), {'c_fatty_acid_saturation', 'c_cholesterol_buffer'})
        self.assertNotIn('correct_option', json.dumps(self.calls[0]))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 1)

    def test_replay_returns_saved_handoff_without_another_tutor_call(self):
        self.assertEqual(self.dispatch(), self.dispatch())
        self.assertEqual(len(self.calls), 1)

    def test_running_dispatch_is_not_started_again(self):
        def running(context):
            result = self.dispatch()
            self.assertEqual(result['operation_status'], 'running')
            self.assertIsNone(result['next_step'])
            return self.tutor(context)
        self.assertEqual(self.dispatch(running)['operation_status'], 'ready')
        self.assertEqual(len(self.calls), 1)

    def test_real_javascript_guard_accepts_claimed_reservation(self):
        def verified(context):
            node = os.environ.get('STUDY_BUDDY_NODE') or shutil.which('node')
            script = """
import {buildTutorInput} from './backend/tutor-input.mjs';
let text = ''; for await (const chunk of process.stdin) text += chunk;
const result = await buildTutorInput(JSON.parse(text));
console.log(result.user);
"""
            result = subprocess.run([node, '--input-type=module', '-e', script], cwd=ROOT,
                                    input=json.dumps(context), capture_output=True, text=True, check=True, timeout=15)
            input_ = json.loads(result.stdout)
            self.assertEqual(set(input_['focus_concept_ids']), {'c_fatty_acid_saturation', 'c_cholesterol_buffer'})
            self.assertNotIn('retryReservation', input_)
            self.assertNotIn('glycocalyx', input_['approved_lesson']['source_text'])
            return self.tutor(context)
        self.assertEqual(self.dispatch(verified)['operation_status'], 'ready')

    def test_wrong_focus_fails_and_does_not_restore_learning_retry(self):
        def wrong(context):
            result = self.tutor(context)
            result['handoff']['payload']['concepts'].pop()
            return result
        result = self.dispatch(wrong)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM explanations WHERE round = 'retry'").fetchone()[0], 0)
        self.assertEqual(self.dispatch(wrong)['operation_status'], 'failed')
        self.assertEqual(len(self.calls), 1)

    def test_tutor_failure_preserves_consumed_technical_budget(self):
        def failed(_): raise TutorFailure('output validation', 1)
        self.assertEqual(self.dispatch(failed)['status'], 'failed')
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs WHERE handoff_id = ?',
                                          (self.route['tutor_handoff_id'],)).fetchone()[0], 1)

    def test_wrong_learner_is_blocked_before_tutor_call(self):
        with self.assertRaises(ReteachingRejected):
            dispatch_reteaching(self.conn, 'learner_z1b2c3d4e5f6', self.attempt_id, tutor=self.tutor)
        self.assertEqual(self.calls, [])

    def test_retry_save_failure_rolls_back_new_explanation(self):
        self.conn.execute("CREATE TRIGGER fail_retry_concepts BEFORE INSERT ON explanation_concepts BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.assertEqual(self.dispatch()['status'], 'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM explanations').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)

    def test_adapter_cannot_mutate_trusted_controller_context(self):
        def changed(context):
            context['sessionId'] = 'session_forged'
            return self.tutor(context)
        self.assertEqual(self.dispatch(changed)['status'], 'failed')

    def test_simultaneous_dispatch_calls_invoke_tutor_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import closing
        import sqlite3
        import tempfile
        import threading
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'retry.sqlite')
            with closing(sqlite3.connect(path)) as disk:
                self.conn.backup(disk)
            barrier = threading.Barrier(2)
            def dispatch(_):
                with closing(sqlite3.connect(path, timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys = ON')
                    barrier.wait(timeout=5)
                    return dispatch_reteaching(conn, LEARNER, self.attempt_id, tutor=self.tutor)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(dispatch, range(2)))
            self.assertEqual(len(self.calls), 1)
            self.assertTrue(all(r['operation_status'] in ('running', 'ready') for r in results))
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM explanations WHERE round = 'retry'").fetchone()[0], 1)
                self.assertEqual(conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)
