"""End-to-end session sequencing with fixture agents, real SQLite state and no API calls.

These prove the controller sequences, budgets and failure handling, not the quality
of any AI-generated teaching, quiz or feedback.
"""
import copy
import json
from pathlib import Path
import sqlite3
import unittest

from study_buddy.answer_keys import get_answer_key
from study_buddy.lessons import load_lessons
from study_buddy.session_flow import run_study_session
from tests.quality_fixtures import passing_review

ROOT = Path(__file__).resolve().parent.parent
LEARNER = 'learner_a1b2c3d4e5f6'
EXAMPLES = ROOT / 'schemas/v1/examples'


class SessionFlowTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.executescript((ROOT / 'db/schema.sql').read_text())
        load_lessons(self.conn)
        self.conn.execute('INSERT INTO learners VALUES (?)', (LEARNER,))
        self.conn.commit()
        self.tutor_calls, self.assessment_calls, self.feedback_calls = [], [], []

    def tutor(self, context):
        self.tutor_calls.append(copy.deepcopy(context))
        name = 'tutor_to_assessment.retry.example.json' if context['round'] == 'retry' else 'tutor_to_assessment.example.json'
        h = json.loads((EXAMPLES / name).read_text())
        h['handoff_id'] = context['handoffId']
        h['context']['learner_session_id'] = context['sessionId']
        h['payload']['explanation_id'] = context['explanationId']
        return dict(handoff=h, repairCount=0)

    def assessment(self, h, *, repair_request=None, **timeouts):
        self.assessment_calls.append(h['context']['round'])
        sample = json.loads((EXAMPLES / 'quiz_output.example.json').read_text())
        if h['context']['round'] == 'first':
            sample.update(handoff_id=h['handoff_id'], explanation_id=h['payload']['explanation_id'])
            return sample
        focus = {c['concept_id'] for c in h['payload']['concepts']}
        pairs = [(q, k) for q, k in zip(sample['quiz']['questions'], sample['answer_key']) if q['concept_id'] in focus]
        questions, keys = [], []
        for i in range(10):
            q, k = copy.deepcopy(pairs[i % len(pairs)])
            q['position'] = k['position'] = i + 1
            questions.append(q); keys.append(k)
        return dict(schema_version='1.0', handoff_id=h['handoff_id'], explanation_id=h['payload']['explanation_id'],
                    round='retry', quiz=dict(questions=questions), answer_key=keys)

    def feedback(self, context, *, repair_request=None, **timeouts):
        self.feedback_calls.append(context['round'])
        return dict(schema_version='1.0', quiz_id=context['quiz_id'], attempt_id=context['attempt_id'],
                    feedback=[dict(position=i['position'], explanation=i['rationale']) for i in context['graded']['items']],
                    weak_concepts=copy.deepcopy(context['graded']['weak_concepts']), summary='Keep going.')

    def learner(self, *wrong_per_round):
        """Answer each round, missing the listed 1-based positions."""
        rounds = iter(wrong_per_round)
        def answer(quiz):
            wrong = set(next(rounds))
            keys = get_answer_key(self.conn, quiz['quiz_id'])
            return {k['question_id']: (('b' if k['correct_option'] == 'a' else 'a') if i in wrong else k['correct_option'])
                    for i, k in enumerate(keys, 1)}
        return answer

    def run_flow(self, answer, **overrides):
        kwargs = dict(tutor=self.tutor, assessment=self.assessment, reviewer=passing_review, feedback=self.feedback)
        kwargs.update(overrides)
        return run_study_session(self.conn, LEARNER, 'lesson_cell_membranes', answer, **kwargs)

    def count(self, table):
        return self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def test_fail_reteach_one_retry_pass_then_summary(self):
        result = self.run_flow(self.learner({3, 4, 6, 7}, set()))
        self.assertEqual((result['status'], result['error']), ('completed', None))
        self.assertEqual([s['step'] for s in result['steps']],
                         ['quiz', 'grade', 'feedback', 'route', 'reteach', 'quiz', 'grade', 'feedback', 'route', 'summary'])
        self.assertEqual(result['scores'], [6, 10])
        self.assertEqual((self.count('quizzes'), self.count('attempts'), self.count('results')), (2, 2, 2))
        self.assertEqual(self.conn.execute('SELECT learning_retry_count, status FROM study_sessions').fetchone(), (1, 'completed'))
        self.assertEqual(self.assessment_calls, ['first', 'retry'])
        self.assertEqual(len(self.tutor_calls), 2)
        self.assertEqual(self.feedback_calls, ['first', 'retry'])
        self.assertIsNotNone(result['summary'])

    def test_pass_on_first_round_skips_reteaching_and_retry(self):
        result = self.run_flow(self.learner({3}))
        self.assertEqual((result['status'], result['scores']), ('completed', [9]))
        self.assertEqual([s['step'] for s in result['steps']], ['quiz', 'grade', 'feedback', 'route', 'summary'])
        self.assertEqual((len(self.tutor_calls), self.count('quizzes')), (1, 1))
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)

    def test_failing_the_retry_still_summarizes_and_never_offers_a_second_retry(self):
        result = self.run_flow(self.learner({3, 4, 6, 7}, {1, 2, 3}))
        self.assertEqual((result['status'], result['scores']), ('completed', [6, 7]))
        self.assertEqual((len(self.tutor_calls), self.count('quizzes'), self.assessment_calls), (2, 2, ['first', 'retry']))
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)

    def test_failed_result_save_records_no_score_and_stops(self):
        self.conn.execute("CREATE TRIGGER fail_result BEFORE INSERT ON results BEGIN SELECT RAISE(ABORT, 'failure'); END")
        self.conn.commit()
        result = self.run_flow(self.learner({1}))
        self.assertEqual(result['status'], 'failed')
        self.assertIn('No score was recorded', result['error'])
        self.assertEqual((self.count('attempts'), self.count('results'), self.count('responses')), (0, 0, 0))
        self.assertEqual(self.feedback_calls, [])
        self.assertIsNone(result['summary'])

    def test_feedback_failure_preserves_the_verified_score_and_does_not_reteach(self):
        def broken(context, **kwargs):
            raise RuntimeError('provider down')
        result = self.run_flow(self.learner({3, 4, 6, 7}), feedback=broken)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['scores'], [6])
        self.assertEqual(self.count('results'), 1)
        self.assertEqual(len(self.tutor_calls), 1)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)
        self.assertNotIn('provider down', json.dumps(result))

    def test_unpublished_quiz_stops_before_any_answers(self):
        def uncertain(context, **kwargs):
            report = passing_review(context)
            report['questions'][0]['grounding'] = 'uncertain'
            report['questions'][0]['uncertainty_notes'] = ['Cannot verify.']
            return report
        def forbidden(quiz):
            self.fail('The learner must not see an unreviewed quiz.')
        result = self.run_flow(forbidden, reviewer=uncertain)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual([s['step'] for s in result['steps']], ['quiz'])
        self.assertEqual((self.count('quizzes'), self.count('attempts')), (0, 0))

    def test_tutor_failure_never_reaches_assessment(self):
        from study_buddy.controller import TutorFailure
        def failing(context):
            raise TutorFailure('model call', 1)
        result = self.run_flow(lambda quiz: self.fail('no quiz'), tutor=failing)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.assessment_calls, [])

    def test_retry_quiz_failure_stops_without_a_summary_or_second_retry(self):
        def retry_breaks(h, **kwargs):
            if h['context']['round'] == 'retry':
                raise RuntimeError('provider down')
            return self.assessment(h, **kwargs)
        result = self.run_flow(self.learner({3, 4, 6, 7}), assessment=retry_breaks)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.count('quizzes'), 1)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)
        self.assertEqual(self.count('attempts'), 1)
        self.assertIsNone(result['summary'])


if __name__ == '__main__':
    unittest.main()
