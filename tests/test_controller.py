"""Offline controller routing tests using SQLite and an injected Tutor runner."""
import copy
import json
from pathlib import Path
import sqlite3
import unittest

from study_buddy.controller import start_session, start_quiz_session, run_tutor, TutorFailure
from study_buddy.lessons import load_lessons

ROOT = Path(__file__).resolve().parent.parent
LEARNER = 'learner_a1b2c3d4e5f6'


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.executescript((ROOT / 'db/schema.sql').read_text())
        load_lessons(self.conn)
        self.conn.execute('INSERT INTO learners VALUES (?)', (LEARNER,))
        self.conn.commit()
        self.calls = []

    def tutor(self, context):
        self.calls.append(copy.deepcopy(context))
        h = json.loads((ROOT / 'schemas/v1/examples/tutor_to_assessment.example.json').read_text())
        h['handoff_id'] = context['handoffId']
        h['context']['learner_session_id'] = context['sessionId']
        h['payload']['explanation_id'] = context['explanationId']
        return dict(handoff=h, repairCount=0)

    def start(self, tutor=None):
        return start_session(self.conn, LEARNER, 'lesson_cell_membranes', tutor=tutor or self.tutor)

    def test_starts_session_supplies_ids_and_saves_valid_explanation(self):
        result = self.start()
        self.assertEqual(len(self.calls), 1)
        for key, prefix in [('sessionId', 'session'), ('handoffId', 'handoff'), ('explanationId', 'expl')]:
            self.assertRegex(self.calls[0][key], f'^{prefix}_[a-z0-9]{{12}}$')
        self.assertEqual(self.calls[0]['round'], 'first')
        self.assertEqual(result['next_step'], 'assessment')
        self.assertEqual(self.conn.execute('SELECT status, learning_retry_count FROM study_sessions').fetchone(), ('teaching', 0))
        self.assertEqual(self.conn.execute('SELECT text FROM explanations').fetchone()[0], result['handoff']['payload']['explanation_text'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM explanation_concepts').fetchone()[0], 7)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 0)

    def test_exhausted_tutor_repair_is_recorded_without_another_tutor_call(self):
        calls = []
        def failed(context):
            calls.append(context)
            raise TutorFailure('output validation', 1)
        result = self.start(failed)
        self.assertEqual(len(calls), 1)
        self.assertEqual((result['status'], result['next_step'], result['handoff']), ('failed', None, None))
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)
        for table in ['explanations', 'quizzes', 'attempts', 'results']:
            self.assertEqual(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)

    def test_stale_handoff_and_changed_source_are_rejected(self):
        for field in ['session', 'source', 'concept']:
            with self.subTest(field=field):
                def changed(context):
                    result = self.tutor(context)
                    h = result['handoff']
                    if field == 'session': h['context']['learner_session_id'] = 'session_stale'
                    if field == 'source': h['sources'][0]['url'] = 'https://example.com'
                    if field == 'concept': h['payload']['concepts'][0]['concept_id'] = 'c_unknown'
                    return result
                self.assertEqual(self.start(changed)['status'], 'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM explanations').fetchone()[0], 0)

    def test_missing_lesson_or_unknown_learner_never_calls_tutor(self):
        with self.assertRaises(ValueError):
            start_session(self.conn, LEARNER, 'lesson_missing', tutor=self.tutor)
        with self.assertRaises(ValueError):
            start_session(self.conn, 'learner_z1b2c3d4e5f6', 'lesson_cell_membranes', tutor=self.tutor)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0], 0)

    def test_save_failure_rolls_back_explanation_and_records_failure(self):
        self.conn.execute("CREATE TRIGGER fail_concepts BEFORE INSERT ON explanation_concepts BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        result = self.start()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM explanations').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'failed')

    def test_provider_exception_is_not_exposed(self):
        def failed(_context): raise RuntimeError('private provider error')
        result = self.start(failed)
        self.assertNotIn('private provider error', json.dumps(result))
        self.assertEqual(result['status'], 'failed')

    def test_real_node_adapter_rejects_bad_context_before_model_call(self):
        with self.assertRaises(TutorFailure) as raised:
            run_tutor({'lessonId': 'lesson_cell_membranes', 'sessionId': 'invalid'})
        self.assertEqual(raised.exception.stage, 'input preparation')
        self.assertEqual(raised.exception.repair_count, 0)

    def assessment(self, handoff, *, repair_request=None):
        self.assessment_calls.append((copy.deepcopy(handoff), repair_request))
        quiz = json.loads((ROOT / 'schemas/v1/examples/quiz_output.example.json').read_text())
        quiz['handoff_id'] = handoff['handoff_id']
        quiz['explanation_id'] = handoff['payload']['explanation_id']
        quiz['round'] = handoff['context']['round']
        return quiz

    def start_quiz(self, assessment=None, tutor=None):
        self.assessment_calls = []
        return start_quiz_session(self.conn, LEARNER, 'lesson_cell_membranes',
                                  tutor=tutor or self.tutor, assessment=assessment or self.assessment)

    def test_validated_tutor_handoff_reaches_assessment_once_and_returns_no_keys(self):
        result = self.start_quiz()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.assessment_calls), 1)
        self.assertEqual(self.assessment_calls[0][0]['handoff_id'], self.calls[0]['handoffId'])
        self.assertIsNone(self.assessment_calls[0][1])
        self.assertEqual(result['status'], 'awaiting_answers')
        self.assertEqual(len(result['quiz']['questions']), 10)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM answer_keys').fetchone()[0], 10)
        for private in ['answer_key', 'correct_option', 'rationale']:
            self.assertNotIn(private, json.dumps(result))
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'awaiting_answers')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)

    def test_tutor_failure_never_calls_assessment(self):
        def failed(_): raise TutorFailure('output validation', 1)
        result = self.start_quiz(tutor=failed)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.assessment_calls, [])

    def test_one_invalid_quiz_can_use_remaining_handoff_repair(self):
        def repairable(handoff, *, repair_request=None):
            quiz = self.assessment(handoff, repair_request=repair_request)
            if repair_request is None: quiz['explanation_id'] = 'expl_stale'
            return quiz
        result = self.start_quiz(assessment=repairable)
        self.assertEqual(result['status'], 'awaiting_answers')
        self.assertEqual(len(self.assessment_calls), 2)
        self.assertIsNotNone(self.assessment_calls[1][1])
        self.assertNotIn('expl_stale', self.assessment_calls[1][1])
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0], 1)

    def test_two_invalid_quizzes_fail_without_persisting_partial_quiz(self):
        def invalid(handoff, *, repair_request=None):
            quiz = self.assessment(handoff, repair_request=repair_request)
            quiz['quiz']['questions'][0]['concept_id'] = 'c_unknown'
            return quiz
        result = self.start_quiz(assessment=invalid)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.assessment_calls), 2)
        for table in ['quizzes', 'questions', 'answer_keys']:
            self.assertEqual(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)

    def test_tutor_repair_budget_is_not_reset_for_assessment(self):
        def repaired_tutor(context):
            result = self.tutor(context)
            result['repairCount'] = 1
            return result
        def invalid(handoff, *, repair_request=None):
            quiz = self.assessment(handoff, repair_request=repair_request)
            quiz['quiz']['questions'].pop()
            return quiz
        result = self.start_quiz(assessment=invalid, tutor=repaired_tutor)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.assessment_calls), 1)

    def test_failed_ready_status_save_rolls_back_questions_and_keys(self):
        self.conn.execute("CREATE TRIGGER fail_ready BEFORE UPDATE ON study_sessions WHEN NEW.status = 'awaiting_answers' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        result = self.start_quiz()
        self.assertEqual(result['status'], 'failed')
        for table in ['quizzes', 'questions', 'answer_keys']:
            self.assertEqual(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM explanations').fetchone()[0], 1)

    def test_assessment_cannot_change_trusted_handoff_by_mutating_its_input(self):
        def mutating(handoff, *, repair_request=None):
            handoff['handoff_id'] = 'handoff_forged'
            return self.assessment(handoff, repair_request=repair_request)
        self.assertEqual(self.start_quiz(assessment=mutating)['status'], 'failed')
