"""Private staging and atomic state transition tests, with synthetic reports."""
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

from tests import test_quality_state as fixtures
from study_buddy.quality_review import candidate_hash, ReviewRejected
from study_buddy.quality_state import (QualityStateRejected, reserve_quality_call, complete_quality_call)
from study_buddy.quality_storage import (store_candidate, load_private_candidate,
                                        store_review_report, load_private_review_report)

ROOT = Path(__file__).resolve().parent.parent
LEARNER = fixtures.LEARNER


class QualityStorageTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.QualityStateTests(); self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn, self.op = self.helper.conn, self.helper.op
        self.candidate = json.loads((ROOT / 'backend/evals/revisions/2026-10-26-manual-quiz-revision.json').read_text())
        self.candidate['handoff_id'] = self.helper.args[2]
        self.candidate['explanation_id'] = self.helper.args[3]
        self.call = reserve_quality_call(self.conn, LEARNER, self.op, 'generation')

    def stage(self):
        return store_candidate(self.conn, LEARNER, self.call['call_id'], self.candidate)

    def report(self, version=0, candidate=None):
        candidate = candidate or self.candidate
        r = json.loads((ROOT / 'tests/fixtures/quiz_quality/pass.json').read_text())['report']
        r.update(operation_id=self.op, session_id=self.helper.args[1], handoff_id=candidate['handoff_id'],
                 explanation_id=candidate['explanation_id'], candidate_version=version, candidate_hash=candidate_hash(candidate))
        keys = {k['position']: k['correct_option'] for k in candidate['answer_key']}
        questions = {q['position']: q for q in candidate['quiz']['questions']}
        for q in r['questions']:
            q['concept_id'] = questions[q['position']]['concept_id']
            q['defensible_options'] = [keys[q['position']]]
        return r

    def prepare_review(self):
        self.stage()
        return reserve_quality_call(self.conn, LEARNER, self.op, 'review')

    def test_questions_and_keys_are_separate_and_round_trip_exactly(self):
        result = self.stage()
        self.assertTrue(result['stored']); self.assertEqual(result['operation']['stage'], 'reviewing')
        data = json.loads(self.conn.execute('SELECT question_data FROM quiz_quality_candidates').fetchone()[0])
        self.assertNotIn('answer_key', data)
        self.assertNotIn('correct_option', json.dumps(data))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_candidate_keys').fetchone()[0], 10)
        self.assertEqual(load_private_candidate(self.conn, LEARNER, self.op, 0), self.candidate)
        for table in ('quizzes', 'questions', 'answer_keys', 'attempts', 'results'):
            self.assertEqual(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)

    def test_non_position_key_array_order_is_preserved_for_hash_binding(self):
        self.candidate['answer_key'].reverse(); self.stage()
        self.assertEqual(load_private_candidate(self.conn, LEARNER, self.op, 0), self.candidate)

    def test_candidate_replay_is_idempotent_and_versions_cannot_be_overwritten(self):
        self.stage(); self.assertFalse(self.stage()['applied'])
        changed = copy.deepcopy(self.candidate); changed['quiz']['questions'][0]['prompt'] += ' Changed.'
        with self.assertRaises(QualityStateRejected): store_candidate(self.conn, LEARNER, self.call['call_id'], changed)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_candidates').fetchone()[0], 1)

    def test_owner_scope_applies_to_private_reads_and_writes(self):
        with self.assertRaises(QualityStateRejected): store_candidate(self.conn, 'learner_other', self.call['call_id'], self.candidate)
        review = self.prepare_review(); r = self.report()
        with self.assertRaises(QualityStateRejected): load_private_candidate(self.conn, 'learner_other', self.op, 0)
        with self.assertRaises(QualityStateRejected): store_review_report(self.conn, 'learner_other', review['call_id'], r)
        store_review_report(self.conn, LEARNER, review['call_id'], r)
        with self.assertRaises(QualityStateRejected): load_private_review_report(self.conn, 'learner_other', self.op, review['call_id'])

    def test_validated_report_and_derived_approval_are_atomic(self):
        review = self.prepare_review(); r = self.report()
        result = store_review_report(self.conn, LEARNER, review['call_id'], r)
        self.assertEqual(result['decision'], 'pass'); self.assertEqual(result['operation']['stage'], 'approved')
        self.assertEqual(load_private_review_report(self.conn, LEARNER, self.op, review['call_id']), r)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 0)
        self.assertNotIn('report_data', result)
        self.assertNotIn('answer_key', json.dumps(result))

    def test_quality_failure_report_is_saved_as_needs_revision(self):
        review = self.prepare_review(); r = self.report()
        q = r['questions'][5]; q['exactly_one_answer'] = 'fail'; q['defensible_options'] = ['a', 'b']
        q['issues'] = [dict(code='ambiguous_answer', detail='Synthetic competing-answer judgment.', related_positions=[])]
        result = store_review_report(self.conn, LEARNER, review['call_id'], r)
        self.assertEqual(result['decision'], 'needs_revision')
        self.assertEqual(result['operation']['stage'], 'needs_revision')
        self.assertEqual(load_private_review_report(self.conn, LEARNER, self.op, review['call_id']), r)

    def test_uncertain_report_is_saved_but_does_not_approve(self):
        review = self.prepare_review(); r = self.report(); q = r['questions'][0]
        q['grounding'] = 'uncertain'; q['uncertainty_notes'] = ['Synthetic uncertainty.']
        result = store_review_report(self.conn, LEARNER, review['call_id'], r)
        self.assertEqual(result['decision'], 'blocked'); self.assertEqual(result['operation']['stage'], 'needs_revision')
        self.assertEqual(self.conn.execute('SELECT decision FROM quiz_quality_reports').fetchone()[0], 'blocked')

    def test_stale_and_malformed_reports_cannot_advance_state(self):
        review = self.prepare_review()
        for mutate in (lambda r: r.update(candidate_hash='0' * 64), lambda r: r.update(overall_pass=True)):
            r = self.report(); mutate(r)
            with self.assertRaises(ReviewRejected): store_review_report(self.conn, LEARNER, review['call_id'], r)
        self.assertEqual(self.conn.execute('SELECT stage FROM quiz_quality_operations').fetchone()[0], 'reviewing')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 0)

    def test_report_replay_is_idempotent_and_cannot_be_rewritten(self):
        review = self.prepare_review(); r = self.report(); store_review_report(self.conn, LEARNER, review['call_id'], r)
        self.assertFalse(store_review_report(self.conn, LEARNER, review['call_id'], r)['applied'])
        r['questions'][0]['uncertainty_notes'] = ['Changed after approval.']
        with self.assertRaises(QualityStateRejected): store_review_report(self.conn, LEARNER, review['call_id'], r)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 1)

    def test_candidate_insert_failure_rolls_back_completed_call_and_state(self):
        self.conn.execute("CREATE TRIGGER fail_candidate BEFORE INSERT ON quiz_quality_candidates BEGIN SELECT RAISE(ABORT, 'test'); END")
        with self.assertRaises(sqlite3.Error): self.stage()
        self.assertEqual(self.conn.execute('SELECT stage, candidate_hash FROM quiz_quality_operations').fetchone(), ('generating', None))
        self.assertEqual(self.conn.execute('SELECT status FROM quiz_quality_calls').fetchone()[0], 'reserved')

    def test_key_insert_failure_rolls_back_entire_candidate(self):
        self.conn.execute("CREATE TRIGGER fail_key BEFORE INSERT ON quiz_quality_candidate_keys WHEN NEW.position = 5 BEGIN SELECT RAISE(ABORT, 'test'); END")
        with self.assertRaises(sqlite3.Error): self.stage()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_candidates').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_candidate_keys').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT status FROM quiz_quality_calls').fetchone()[0], 'reserved')

    def test_report_insert_failure_rolls_back_approval_and_call_completion(self):
        review = self.prepare_review()
        self.conn.execute("CREATE TRIGGER fail_review BEFORE INSERT ON quiz_quality_reports BEGIN SELECT RAISE(ABORT, 'test'); END")
        with self.assertRaises(sqlite3.Error): store_review_report(self.conn, LEARNER, review['call_id'], self.report())
        self.assertEqual(self.conn.execute('SELECT stage FROM quiz_quality_operations').fetchone()[0], 'reviewing')
        self.assertEqual(self.conn.execute('SELECT status FROM quiz_quality_calls WHERE call_id = ?', (review['call_id'],)).fetchone()[0], 'reserved')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 0)

    def test_tampered_or_incomplete_keys_and_changed_teaching_fail_private_load(self):
        self.stage()
        self.conn.execute("UPDATE quiz_quality_candidate_keys SET correct_option = 'd' WHERE position = 1")
        with self.assertRaises(QualityStateRejected): load_private_candidate(self.conn, LEARNER, self.op, 0)

    def test_missing_key_fails_private_load(self):
        self.stage(); self.conn.execute('DELETE FROM quiz_quality_candidate_keys WHERE position = 1')
        with self.assertRaises(QualityStateRejected): load_private_candidate(self.conn, LEARNER, self.op, 0)

    def test_changed_teaching_invalidates_staged_context(self):
        self.stage(); self.conn.execute("UPDATE explanations SET text = text || ' Changed.'")
        with self.assertRaises(QualityStateRejected): load_private_candidate(self.conn, LEARNER, self.op, 0)

    def test_tampered_report_fails_audit_load(self):
        review = self.prepare_review(); store_review_report(self.conn, LEARNER, review['call_id'], self.report())
        self.conn.execute("UPDATE quiz_quality_reports SET report_fingerprint = 'tampered'")
        with self.assertRaises(QualityStateRejected): load_private_review_report(self.conn, LEARNER, self.op, review['call_id'])

    def test_two_candidate_versions_and_reports_survive_revision(self):
        review = self.prepare_review(); first = self.report(); first['quiz_review']['concept_coverage'] = 'fail'
        first['quiz_review']['issues'] = [dict(code='missing_coverage', detail='Synthetic repeated-target judgment.', related_positions=[1, 2])]
        store_review_report(self.conn, LEARNER, review['call_id'], first)
        revision = reserve_quality_call(self.conn, LEARNER, self.op, 'revision')
        revised = copy.deepcopy(self.candidate); revised['quiz']['questions'][0]['prompt'] += ' Revised.'
        store_candidate(self.conn, LEARNER, revision['call_id'], revised)
        final = reserve_quality_call(self.conn, LEARNER, self.op, 'review_revision')
        store_review_report(self.conn, LEARNER, final['call_id'], self.report(1, revised))
        self.assertEqual(load_private_candidate(self.conn, LEARNER, self.op, 0), self.candidate)
        self.assertEqual(load_private_candidate(self.conn, LEARNER, self.op, 1), revised)
        self.assertEqual(load_private_review_report(self.conn, LEARNER, self.op, review['call_id']), first)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_candidate_keys').fetchone()[0], 20)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 2)

    def test_expired_candidate_result_leaves_no_staging_payload(self):
        self.helper.expire(); result = self.stage()
        self.assertFalse(result['stored']); self.assertEqual(result['operation']['stage'], 'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_candidates').fetchone()[0], 0)

    def test_expired_review_cannot_save_report_or_approval(self):
        review = self.prepare_review(); self.helper.expire()
        result = store_review_report(self.conn, LEARNER, review['call_id'], self.report())
        self.assertFalse(result['stored']); self.assertEqual(result['operation']['stage'], 'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 0)

    def test_duplicate_simultaneous_candidate_storage_has_one_committed_result(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'staging.sqlite')
            with closing(sqlite3.connect(path)) as disk: self.conn.backup(disk)
            barrier = threading.Barrier(2)
            def store(_):
                with closing(sqlite3.connect(path, timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys = ON'); barrier.wait(timeout=5)
                    return store_candidate(conn, LEARNER, self.call['call_id'], self.candidate)
            with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(store, range(2)))
            self.assertEqual(sum(r['applied'] for r in results), 1)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(load_private_candidate(conn, LEARNER, self.op, 0), self.candidate)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM quiz_quality_candidates').fetchone()[0], 1)

    def test_additive_migration_preserves_reserved_operation(self):
        for table in ('quiz_quality_reports', 'quiz_quality_candidate_keys', 'quiz_quality_candidates'):
            self.conn.execute(f'DROP TABLE {table}')
        self.conn.executescript((ROOT / 'db/migrations/006_quiz_quality_storage.sql').read_text())
        self.assertTrue(self.stage()['stored'])
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_hash_only_completion_is_not_silently_backfilled(self):
        complete_quality_call(self.conn, LEARNER, self.call['call_id'], outcome='valid', candidate=self.candidate)
        with self.assertRaises(QualityStateRejected): self.stage()

    def test_retry_staging_keeps_focus_and_does_not_publish_another_quiz(self):
        from tests import test_retry_quiz as retry_fixtures
        from study_buddy.quality_state import start_quality_operation
        helper = retry_fixtures.RetryQuizTests(); helper.setUp(); self.addCleanup(helper.doCleanups)
        conn = helper.conn
        sid, context = conn.execute("SELECT session_id, context FROM session_routes WHERE route = 'reteach'").fetchone()
        h = json.loads(context)['tutor_handoff']
        op = start_quality_operation(conn, LEARNER, sid, h['handoff_id'], h['payload']['explanation_id'], 'retry')['operation']['operation_id']
        call = reserve_quality_call(conn, LEARNER, op, 'generation')
        candidate = helper.assessment(h)
        outside = copy.deepcopy(candidate); outside['quiz']['questions'][0]['concept_id'] = 'c_glycocalyx_recognition'
        with self.assertRaises(QualityStateRejected): store_candidate(conn, LEARNER, call['call_id'], outside)
        store_candidate(conn, LEARNER, call['call_id'], candidate)
        loaded = load_private_candidate(conn, LEARNER, op, 0)
        self.assertEqual({q['concept_id'] for q in loaded['quiz']['questions']}, {'c_fatty_acid_saturation', 'c_cholesterol_buffer'})
        self.assertEqual(conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 1)
