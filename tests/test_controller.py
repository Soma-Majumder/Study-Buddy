"""Offline controller routing tests using SQLite and an injected Tutor runner."""
import copy
import json
from pathlib import Path
import sqlite3
import unittest

from study_buddy.controller import start_session, run_tutor, TutorFailure
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
