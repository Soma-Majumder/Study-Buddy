"""Focused retry quiz routing with fixture agents and actual saved state."""
import copy
import json
from pathlib import Path
import unittest

from tests import test_reteaching as fixtures
from study_buddy.controller import dispatch_retry_quiz, submit_answers, explain_results, decide_next_step, SubmissionRejected
from study_buddy.retry_quiz import RetryQuizRejected
from study_buddy.answer_keys import get_answer_key

ROOT = Path(__file__).resolve().parent.parent
LEARNER = fixtures.LEARNER


class RetryQuizTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReteachingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.conn = self.fixture.conn
        self.attempt_id = self.fixture.attempt_id
        self.fixture.dispatch()
        self.calls = []

    def assessment(self, h, *, repair_request=None):
        self.calls.append((copy.deepcopy(h), repair_request))
        sample = json.loads((ROOT / 'schemas/v1/examples/quiz_output.example.json').read_text())
        focus = {c['concept_id'] for c in h['payload']['concepts']}
        # Test-only Assessment: repeat the source's focused fixture questions.
        # This tests routing/keys, not question novelty or AI generation quality.
        pairs = [(q, k) for q, k in zip(sample['quiz']['questions'], sample['answer_key']) if q['concept_id'] in focus]
        questions, keys = [], []
        for i in range(10):
            q, k = copy.deepcopy(pairs[i % len(pairs)])
            q['position'] = k['position'] = i + 1
            questions.append(q); keys.append(k)
        return dict(schema_version='1.0', handoff_id=h['handoff_id'], explanation_id=h['payload']['explanation_id'],
                    round='retry', quiz=dict(questions=questions), answer_key=keys)

    def dispatch(self, assessment=None):
        return dispatch_retry_quiz(self.conn, LEARNER, self.attempt_id, assessment=assessment or self.assessment)

    def test_focused_ten_question_quiz_is_saved_without_client_keys(self):
        result = self.dispatch()
        self.assertEqual((result['operation_status'], result['status']), ('ready', 'awaiting_answers'))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(result['quiz']['questions']), 10)
        self.assertNotIn('correct_option', json.dumps(result))
        self.assertNotIn('rationale', json.dumps(result))
        qid = result['quiz']['quiz_id']
        self.assertEqual(self.conn.execute('SELECT round FROM quizzes WHERE quiz_id = ?', (qid,)).fetchone()[0], 'retry')
        actual = {r[0] for r in self.conn.execute('SELECT concept_id FROM questions WHERE quiz_id = ?', (qid,))}
        self.assertEqual(actual, {'c_fatty_acid_saturation', 'c_cholesterol_buffer'})
        self.assertEqual(len(get_answer_key(self.conn, qid)), 10)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 2)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)

    def test_replay_and_running_operation_do_not_start_another_quiz(self):
        def running(h, *, repair_request=None):
            result = self.dispatch()
            self.assertEqual(result['operation_status'], 'running')
            self.assertIsNone(result['quiz'])
            return self.assessment(h, repair_request=repair_request)
        first = self.dispatch(running)
        self.assertEqual(first, self.dispatch())
        self.assertEqual(len(self.calls), 1)

    def test_out_of_focus_output_fails_after_one_remaining_repair(self):
        def invalid(h, *, repair_request=None):
            result = self.assessment(h, repair_request=repair_request)
            result['quiz']['questions'][0]['concept_id'] = 'c_glycocalyx_recognition'
            return result
        self.assertEqual(self.dispatch(invalid)['status'], 'failed')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.dispatch(invalid)['status'], 'failed')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)

    def test_tutor_spent_repair_cannot_be_refreshed_for_retry_quiz(self):
        self.conn.execute('UPDATE handoff_repairs SET repair_count = 1 WHERE handoff_id = ?',
                          (self.fixture.route['tutor_handoff_id'],))
        self.conn.commit()
        def invalid(h, *, repair_request=None):
            result = self.assessment(h, repair_request=repair_request)
            result['round'] = 'first'
            return result
        self.assertEqual(self.dispatch(invalid)['status'], 'failed')
        self.assertEqual(len(self.calls), 1)

    def test_retry_quiz_save_failure_preserves_first_quiz_and_grade(self):
        self.conn.execute("CREATE TRIGGER fail_retry_ready BEFORE UPDATE ON study_sessions WHEN NEW.status = 'awaiting_answers' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.assertEqual(self.dispatch()['status'], 'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM answer_keys').fetchone()[0], 10)
        self.assertEqual(self.conn.execute('SELECT score FROM results').fetchone()[0], 6)

    def test_wrong_learner_cannot_dispatch(self):
        with self.assertRaises(RetryQuizRejected):
            dispatch_retry_quiz(self.conn, 'learner_z1b2c3d4e5f6', self.attempt_id, assessment=self.assessment)
        self.assertEqual(self.calls, [])

    def test_low_scoring_retry_routes_to_summary_not_another_reteaching_loop(self):
        ready = self.dispatch()
        qid = ready['quiz']['quiz_id']
        keys = get_answer_key(self.conn, qid)
        answers = {k['question_id']: 'b' if k['correct_option'] == 'a' else 'a' for k in keys}
        result = submit_answers(self.conn, LEARNER, qid, answers)
        self.assertEqual(result['graded']['score'], 0)
        def feedback(c, *, repair_request=None):
            return dict(schema_version='1.0', quiz_id=c['quiz_id'], attempt_id=c['attempt_id'],
                        feedback=[dict(position=i['position'], explanation=i['rationale']) for i in c['graded']['items']],
                        weak_concepts=copy.deepcopy(c['graded']['weak_concepts']), summary='Here are your practice results.')
        self.assertEqual(explain_results(self.conn, LEARNER, result['attempt_id'], assessment=feedback)['operation_status'], 'ready')
        final = decide_next_step(self.conn, LEARNER, result['attempt_id'])
        self.assertEqual((final['next_step'], final['reason'], final['status']), ('summary', 'retry_finished', 'pending_save'))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM session_routes WHERE route = 'reteach'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 2)
        self.assertIsNone(self.dispatch()['next_step'])

    def test_original_quiz_cannot_be_resubmitted_while_retry_awaits_answers(self):
        retry = self.dispatch()
        old_quiz_id = self.fixture.source['quiz_id']
        old_keys = get_answer_key(self.conn, old_quiz_id)
        with self.assertRaises(SubmissionRejected):
            submit_answers(self.conn, LEARNER, old_quiz_id,
                           {k['question_id']: k['correct_option'] for k in old_keys})
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'awaiting_answers')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
        self.assertEqual(self.dispatch()['quiz']['quiz_id'], retry['quiz']['quiz_id'])

    def test_additive_migration_allows_existing_session_to_create_retry_quiz(self):
        self.conn.execute('DROP TABLE retry_quiz_operations')
        self.conn.executescript((ROOT / 'db/migrations/003_retry_quiz_operations.sql').read_text())
        self.assertEqual(self.dispatch()['status'], 'awaiting_answers')
