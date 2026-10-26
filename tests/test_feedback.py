"""Offline feedback routing using actual saved controller grading."""
import copy
import json
from pathlib import Path
import sqlite3
import unittest

from study_buddy.controller import start_quiz_session, submit_answers, explain_results
from study_buddy.feedback import FeedbackRejected
from study_buddy.answer_keys import get_answer_key
from study_buddy.lessons import load_lessons

ROOT = Path(__file__).resolve().parent.parent
LEARNER = 'learner_a1b2c3d4e5f6'


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.executescript((ROOT / 'db/schema.sql').read_text())
        load_lessons(self.conn)
        self.conn.execute('INSERT INTO learners VALUES (?)', (LEARNER,))
        self.conn.commit()
        def tutor(context):
            h = json.loads((ROOT / 'schemas/v1/examples/tutor_to_assessment.example.json').read_text())
            h['handoff_id'] = context['handoffId']
            h['context']['learner_session_id'] = context['sessionId']
            h['payload']['explanation_id'] = context['explanationId']
            return dict(handoff=h, repairCount=0)
        def quiz(h, *, repair_request=None):
            q = json.loads((ROOT / 'schemas/v1/examples/quiz_output.example.json').read_text())
            q.update(handoff_id=h['handoff_id'], explanation_id=h['payload']['explanation_id'])
            return q
        self.started = start_quiz_session(self.conn, LEARNER, 'lesson_cell_membranes', tutor=tutor, assessment=quiz)
        self.quiz_id = self.started['quiz']['quiz_id']
        keys = get_answer_key(self.conn, self.quiz_id)
        answers = {k['question_id']: ('b' if k['correct_option'] == 'a' else 'a') if i in (3, 4, 6, 7) else k['correct_option']
                   for i, k in enumerate(keys, 1)}
        self.result = submit_answers(self.conn, LEARNER, self.quiz_id, answers)
        self.attempt_id = self.result['attempt_id']
        self.calls = []

    def feedback(self, context, *, repair_request=None):
        self.calls.append((copy.deepcopy(context), repair_request))
        return dict(schema_version='1.0', quiz_id=context['quiz_id'], attempt_id=context['attempt_id'],
                    feedback=[dict(position=i['position'], explanation=i['rationale']) for i in context['graded']['items']],
                    weak_concepts=copy.deepcopy(context['graded']['weak_concepts']), summary='Review the missed concepts.')

    def explain(self, assessment=None):
        return explain_results(self.conn, LEARNER, self.attempt_id, assessment=assessment or self.feedback)

    def test_saved_grading_drives_feedback_and_never_changes_score(self):
        result = self.explain()
        self.assertEqual((result['operation_status'], result['status'], result['next_step']), ('ready', 'grading', 'retry_decision'))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0]['graded']['score'], 6)
        stored = self.conn.execute('SELECT score, passed, feedback FROM results').fetchone()
        self.assertEqual(stored[:2], (6, 0))
        self.assertEqual(json.loads(stored[2]), result['feedback'])
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)

    def test_completed_feedback_is_reused_without_new_agent_call(self):
        first = self.explain()
        second = self.explain()
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM feedback_operations').fetchone()[0], 1)

    def test_running_operation_is_not_dispatched_again(self):
        def concurrent(context, *, repair_request=None):
            existing = self.explain()
            self.assertEqual(existing['operation_status'], 'running')
            self.assertIsNone(existing['next_step'])
            return self.feedback(context, repair_request=repair_request)
        self.assertEqual(self.explain(concurrent)['operation_status'], 'ready')
        self.assertEqual(len(self.calls), 1)

    def test_one_invalid_feedback_is_repaired_with_original_grading(self):
        def stale(context, *, repair_request=None):
            out = self.feedback(context, repair_request=repair_request)
            if repair_request is None: out['attempt_id'] = 'attempt_stale'
            return out
        self.assertEqual(self.explain(stale)['operation_status'], 'ready')
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn('attempt_stale', self.calls[1][1])
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs WHERE direction = ?',
                                          ('assessment_to_tutor',)).fetchone()[0], 1)

    def test_duplicate_or_missing_weak_concepts_fail_after_one_repair(self):
        def invalid(context, *, repair_request=None):
            out = self.feedback(context, repair_request=repair_request)
            out['weak_concepts'].append(copy.deepcopy(out['weak_concepts'][0]))
            return out
        result = self.explain(invalid)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.calls), 2)
        self.assertIsNone(result['feedback'])
        self.assertEqual(self.conn.execute('SELECT score FROM results').fetchone()[0], 6)
        self.explain(invalid)
        self.assertEqual(len(self.calls), 2)

    def test_invalid_output_variants_cannot_be_saved(self):
        # A new independent attempt per subtest keeps persisted budgets separate.
        for kind in ['missing_weak', 'extra_score', 'wrong_quiz', 'wrong_count', 'missing_feedback', 'blank']:
            with self.subTest(kind=kind):
                other = FeedbackTests(); other.setUp()
                try:
                    def invalid(context, *, repair_request=None):
                        out = other.feedback(context, repair_request=repair_request)
                        if kind == 'missing_weak': out['weak_concepts'] = []
                        if kind == 'extra_score': out['score'] = 10
                        if kind == 'wrong_quiz': out['quiz_id'] = 'quiz_stale'
                        if kind == 'wrong_count': out['weak_concepts'][0]['missed_count'] = 9
                        if kind == 'missing_feedback': out['feedback'].pop()
                        if kind == 'blank': out['summary'] = ' '
                        return out
                    self.assertEqual(other.explain(invalid)['operation_status'], 'failed')
                    self.assertEqual(len(other.calls), 2)
                finally:
                    other.doCleanups()

    def test_unauthorized_learner_never_calls_agent(self):
        with self.assertRaises(FeedbackRejected):
            explain_results(self.conn, 'learner_z1b2c3d4e5f6', self.attempt_id, assessment=self.feedback)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM feedback_operations').fetchone()[0], 0)

    def test_feedback_save_failure_preserves_original_grading(self):
        old = self.conn.execute('SELECT feedback FROM results').fetchone()[0]
        self.conn.execute("CREATE TRIGGER fail_feedback BEFORE UPDATE OF feedback ON results BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        result = self.explain()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.conn.execute('SELECT score, feedback FROM results').fetchone(), (6, old))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)

    def test_existing_database_migration_adds_operation_table(self):
        with sqlite3.connect(':memory:') as conn:
            self.addCleanup(conn.close)
            conn.executescript((ROOT / 'db/schema.sql').read_text())
            conn.execute('DROP TABLE feedback_operations')
            conn.executescript((ROOT / 'db/migrations/001_feedback_operations.sql').read_text())
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM feedback_operations').fetchone()[0], 0)
