"""Persisted limits and concurrency tests; no model calls or publication."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from tests import test_controller as fixtures
from study_buddy.quality_review import candidate_hash
from study_buddy.quality_state import (QualityStateRejected, start_quality_operation,
                                      reserve_quality_call, complete_quality_call)

ROOT = Path(__file__).resolve().parent.parent
LEARNER = fixtures.LEARNER


class QualityStateTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.ControllerTests(); self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn = self.helper.conn
        started = self.helper.start()
        h = started['handoff']
        self.args = (LEARNER, started['session_id'], h['handoff_id'], h['payload']['explanation_id'], 'first')
        self.op = start_quality_operation(self.conn, *self.args)['operation']['operation_id']
        self.candidate = json.loads((ROOT / 'schemas/v1/examples/quiz_output.example.json').read_text())
        self.candidate['handoff_id'] = h['handoff_id']
        self.candidate['explanation_id'] = h['payload']['explanation_id']

    def reserve(self, kind):
        return reserve_quality_call(self.conn, LEARNER, self.op, kind)

    def finish(self, call, outcome, **kwargs):
        return complete_quality_call(self.conn, LEARNER, call['call_id'], outcome=outcome, **kwargs)

    def candidate_ready(self):
        call = self.reserve('generation')
        self.finish(call, 'valid', candidate=self.candidate)

    def needs_revision(self):
        self.candidate_ready()
        review = self.reserve('review')
        self.finish(review, 'needs_revision', reviewed_hash=candidate_hash(self.candidate))

    def expire(self):
        self.conn.execute('UPDATE quiz_quality_operations SET started_at = ?, deadline_at = ? WHERE operation_id = ?',
                          (time.time() - 190, time.time() - 10, self.op)); self.conn.commit()

    def test_repeated_start_and_reservation_do_not_refresh_state_or_deadline(self):
        before = start_quality_operation(self.conn, *self.args)
        first = self.reserve('generation'); second = self.reserve('generation')
        self.assertTrue(first['reserved']); self.assertFalse(second['reserved'])
        self.assertEqual(first['call_id'], second['call_id'])
        self.assertEqual(before['operation']['deadline_at'], second['operation']['deadline_at'])
        self.assertEqual(second['reserved_call_count'], 1)
        self.assertFalse(before['created'])

    def test_first_pass_is_two_calls_and_does_not_publish(self):
        self.candidate_ready(); review = self.reserve('review')
        result = self.finish(review, 'pass', reviewed_hash=candidate_hash(self.candidate))
        self.assertEqual(result['operation']['stage'], 'approved')
        self.assertEqual(result['reserved_call_count'], 2)
        self.assertEqual(result['operation']['review_count'], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT status, learning_retry_count FROM study_sessions').fetchone(), ('teaching', 0))

    def test_maximum_five_calls_share_one_technical_repair(self):
        self.finish(self.reserve('generation'), 'invalid')
        self.finish(self.reserve('technical_repair'), 'valid', candidate=self.candidate)
        self.finish(self.reserve('review'), 'needs_revision', reviewed_hash=candidate_hash(self.candidate))
        self.candidate['quiz']['questions'][0]['prompt'] += ' Revised.'
        self.finish(self.reserve('revision'), 'valid', candidate=self.candidate)
        result = self.finish(self.reserve('review_revision'), 'pass', reviewed_hash=candidate_hash(self.candidate))
        self.assertEqual((result['reserved_call_count'], result['operation']['revision_count'], result['operation']['review_count']), (5, 1, 2))
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0], 1)
        self.assertFalse(self.reserve('revision')['reserved'])

    def test_technical_repair_can_instead_be_spent_on_revision(self):
        self.needs_revision()
        self.finish(self.reserve('revision'), 'invalid')
        self.finish(self.reserve('technical_repair'), 'valid', candidate=self.candidate)
        result = self.finish(self.reserve('review_revision'), 'pass', reviewed_hash=candidate_hash(self.candidate))
        self.assertEqual(result['reserved_call_count'], 5)
        self.assertEqual(result['operation']['stage'], 'approved')

    def test_rejected_revision_is_terminal_no_third_candidate(self):
        self.needs_revision()
        self.finish(self.reserve('revision'), 'valid', candidate=self.candidate)
        result = self.finish(self.reserve('review_revision'), 'needs_revision', reviewed_hash=candidate_hash(self.candidate))
        self.assertEqual(result['operation']['failure_code'], 'content_revision_exhausted')
        self.assertFalse(self.reserve('revision')['reserved'])
        with self.assertRaises(QualityStateRejected): self.reserve('technical_repair')
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)

    def test_tutor_spent_repair_is_not_reset(self):
        self.conn.execute('UPDATE handoff_repairs SET repair_count = 1'); self.conn.commit()
        result = self.finish(self.reserve('generation'), 'invalid')
        self.assertEqual(result['operation']['failure_code'], 'technical_repair_exhausted')
        with self.assertRaises(QualityStateRejected): self.reserve('technical_repair')
        self.assertEqual(result['reserved_call_count'], 1)

    def test_uncertain_review_and_provider_failure_stop_without_retry(self):
        self.candidate_ready()
        result = self.finish(self.reserve('review'), 'blocked', reviewed_hash=candidate_hash(self.candidate))
        self.assertEqual(result['operation']['failure_code'], 'review_uncertainty')
        with self.assertRaises(QualityStateRejected): self.reserve('revision')

    def test_provider_failure_is_durable_and_repeat_does_not_dispatch(self):
        call = self.reserve('generation'); result = self.finish(call, 'error')
        self.assertEqual(result['operation']['failure_code'], 'provider_failed')
        self.assertFalse(self.reserve('generation')['reserved'])
        self.assertFalse(self.finish(call, 'error')['applied'])

    def test_wrong_learner_and_wrong_stage_are_rejected(self):
        with self.assertRaises(QualityStateRejected): reserve_quality_call(self.conn, 'learner_other', self.op, 'generation')
        with self.assertRaises(QualityStateRejected): self.reserve('review')
        with self.assertRaises(QualityStateRejected): self.reserve('review_revision')
        with self.assertRaises(QualityStateRejected): self.reserve('revision')
        with self.assertRaises(QualityStateRejected): self.reserve('unknown')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_calls').fetchone()[0], 0)

    def test_finished_call_cannot_be_overwritten_or_recompleted_differently(self):
        call = self.reserve('generation'); self.finish(call, 'valid', candidate=self.candidate)
        self.assertFalse(self.finish(call, 'valid', candidate=self.candidate)['applied'])
        changed = copy.deepcopy(self.candidate); changed['answer_key'][0]['rationale'] += ' Changed.'
        with self.assertRaises(QualityStateRejected): self.finish(call, 'valid', candidate=changed)
        with self.assertRaises(QualityStateRejected): self.finish(call, 'invalid')

    def test_stale_review_hash_and_invalid_candidate_cannot_advance_state(self):
        bad = copy.deepcopy(self.candidate); bad['quiz']['questions'].pop()
        call = self.reserve('generation')
        with self.assertRaises(QualityStateRejected): self.finish(call, 'valid', candidate=bad)
        self.finish(call, 'valid', candidate=self.candidate)
        review = self.reserve('review')
        with self.assertRaises(QualityStateRejected): self.finish(review, 'pass', reviewed_hash='0' * 64)
        self.assertEqual(self.conn.execute('SELECT stage FROM quiz_quality_operations').fetchone()[0], 'reviewing')

    def test_deadline_blocks_new_reservation_and_late_completion(self):
        self.expire(); result = self.reserve('generation')
        self.assertFalse(result['reserved']); self.assertEqual(result['operation']['failure_code'], 'quality_deadline')
        self.assertEqual(result['reserved_call_count'], 0)

    def test_late_response_does_not_advance_to_review(self):
        call = self.reserve('generation'); self.expire()
        result = self.finish(call, 'valid', candidate=self.candidate)
        self.assertFalse(result['applied']); self.assertEqual(result['operation']['failure_code'], 'quality_deadline')
        self.assertIsNone(result['operation']['candidate_hash'])

    def test_reservation_rollback_restores_shared_repair_and_stage(self):
        self.finish(self.reserve('generation'), 'invalid')
        self.conn.execute("CREATE TRIGGER fail_call BEFORE INSERT ON quiz_quality_calls WHEN NEW.kind = 'technical_repair' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.Error): self.reserve('technical_repair')
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT stage FROM quiz_quality_operations').fetchone()[0], 'generation_invalid')

    def test_result_save_rollback_preserves_reserved_call(self):
        call = self.reserve('generation')
        self.conn.execute("CREATE TRIGGER fail_result BEFORE UPDATE ON quiz_quality_calls BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.Error): self.finish(call, 'valid', candidate=self.candidate)
        self.assertEqual(self.conn.execute('SELECT stage, candidate_hash FROM quiz_quality_operations').fetchone(), ('generating', None))
        self.assertEqual(self.conn.execute('SELECT status FROM quiz_quality_calls').fetchone()[0], 'reserved')

    def test_database_constraints_reject_counter_overflow(self):
        for field, value in [('revision_count', 2), ('review_count', 3), ('candidate_version', 2)]:
            with self.subTest(field=field), self.assertRaises(sqlite3.Error), self.conn:
                self.conn.execute(f'UPDATE quiz_quality_operations SET {field} = ?', (value,))

    def test_additive_migration_preserves_existing_explanation(self):
        self.conn.execute('DROP TABLE quiz_quality_calls'); self.conn.execute('DROP TABLE quiz_quality_operations')
        before = self.conn.execute('SELECT COUNT(*) FROM explanations').fetchone()[0]
        self.conn.executescript((ROOT / 'db/migrations/005_quiz_quality_operations.sql').read_text())
        result = start_quality_operation(self.conn, *self.args)
        self.assertTrue(result['created'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM explanations').fetchone()[0], before)

    def test_restart_and_simultaneous_calls_have_one_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'quality.sqlite')
            with closing(sqlite3.connect(path)) as disk: self.conn.backup(disk)
            barrier = threading.Barrier(2)
            def reserve(_):
                with closing(sqlite3.connect(path, timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys = ON'); barrier.wait(timeout=5)
                    return reserve_quality_call(conn, LEARNER, self.op, 'generation')
            with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(reserve, range(2)))
            self.assertEqual(sum(r['reserved'] for r in results), 1)
            self.assertEqual(results[0]['call_id'], results[1]['call_id'])
            with closing(sqlite3.connect(path)) as conn:
                again = start_quality_operation(conn, *self.args)
                self.assertEqual(again['reserved_call_count'], 1)
                self.assertFalse(reserve_quality_call(conn, LEARNER, self.op, 'generation')['reserved'])

    def test_session_that_already_accepts_answers_cannot_dispatch_quality_calls(self):
        self.conn.execute("UPDATE study_sessions SET status = 'awaiting_answers'"); self.conn.commit()
        with self.assertRaises(QualityStateRejected): self.reserve('generation')

    def test_timeouts_fit_the_remaining_deadline(self):
        call = self.reserve('generation')
        self.assertEqual(call['provider_timeout_ms'], 60000)
        self.assertEqual(call['process_timeout_seconds'], 70)

    def test_focused_retry_reservation_preserves_learning_count_and_scope(self):
        from tests import test_retry_quiz as retry_fixtures
        helper = retry_fixtures.RetryQuizTests(); helper.setUp()
        self.addCleanup(helper.doCleanups)
        conn = helper.conn
        row = conn.execute("SELECT session_id, context FROM session_routes WHERE route = 'reteach'").fetchone()
        saved = json.loads(row[1]); h = saved['tutor_handoff']
        operation = start_quality_operation(conn, LEARNER, row[0], h['handoff_id'], h['payload']['explanation_id'], 'retry')
        op_id = operation['operation']['operation_id']
        call = reserve_quality_call(conn, LEARNER, op_id, 'generation')
        candidate = helper.assessment(h)
        result = complete_quality_call(conn, LEARNER, call['call_id'], outcome='valid', candidate=candidate)
        self.assertEqual(result['operation']['stage'], 'reviewing')
        self.assertEqual(conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 1)

    def test_remaining_deadline_caps_both_timeouts(self):
        self.conn.execute('UPDATE quiz_quality_operations SET deadline_at = ? WHERE operation_id = ?', (time.time() + 10, self.op))
        self.conn.commit()
        call = self.reserve('generation')
        self.assertLessEqual(call['process_timeout_seconds'], 10)
        self.assertLess(call['provider_timeout_ms'] / 1000, call['process_timeout_seconds'])
