"""Offline controller routing tests using SQLite and an injected Tutor runner."""
from tests.quality_fixtures import passing_review
import copy
import json
from pathlib import Path
import sqlite3
import unittest

from study_buddy.controller import start_session, start_quiz_session, run_tutor, TutorFailure, submit_answers, SubmissionRejected
from study_buddy.lessons import load_lessons
from study_buddy.answer_keys import get_answer_key

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

    def assessment(self, handoff, *, repair_request=None, **timeouts):
        self.assessment_calls.append((copy.deepcopy(handoff), repair_request))
        quiz = json.loads((ROOT / 'schemas/v1/examples/quiz_output.example.json').read_text())
        quiz['handoff_id'] = handoff['handoff_id']
        quiz['explanation_id'] = handoff['payload']['explanation_id']
        quiz['round'] = handoff['context']['round']
        return quiz

    def start_quiz(self, assessment=None, tutor=None):
        self.assessment_calls = []
        return start_quiz_session(self.conn, LEARNER, 'lesson_cell_membranes',
                                  tutor=tutor or self.tutor, assessment=assessment or self.assessment, reviewer=passing_review)

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
        def repairable(handoff, *, repair_request=None, **timeouts):
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
        def invalid(handoff, *, repair_request=None, **timeouts):
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
        def invalid(handoff, *, repair_request=None, **timeouts):
            quiz = self.assessment(handoff, repair_request=repair_request)
            quiz['quiz']['questions'].pop()
            return quiz
        result = self.start_quiz(assessment=invalid, tutor=repaired_tutor)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.assessment_calls), 1)

    def test_failed_ready_status_save_rolls_back_questions_and_keys(self):
        self.conn.execute("CREATE TRIGGER fail_ready BEFORE UPDATE ON study_sessions WHEN NEW.status = 'awaiting_answers' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        result = self.start_quiz()
        self.assertEqual(result['status'], 'teaching')
        self.assertEqual(result['error'], 'quality_publish_save_failed')
        for table in ['quizzes', 'questions', 'answer_keys']:
            self.assertEqual(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM explanations').fetchone()[0], 1)

    def test_assessment_cannot_change_trusted_handoff_by_mutating_its_input(self):
        def mutating(handoff, *, repair_request=None, **timeouts):
            handoff['handoff_id'] = 'handoff_forged'
            return self.assessment(handoff, repair_request=repair_request)
        self.assertEqual(self.start_quiz(assessment=mutating)['status'], 'failed')

    def answers_for(self, quiz_id, wrong_positions=()):
        keys = get_answer_key(self.conn, quiz_id)
        return {k['question_id']: (
            ('b' if k['correct_option'] == 'a' else 'a') if i in wrong_positions else k['correct_option']
        ) for i, k in enumerate(keys, 1)}

    def test_submission_grades_thresholds_and_saves_concept_evidence(self):
        for wrong, score, passed in [((), 10, True), ((1, 2), 8, True), ((1, 2, 3), 7, False), ((3, 4, 6, 7), 6, False)]:
            with self.subTest(score=score):
                started = self.start_quiz()
                qid = started['quiz']['quiz_id']
                result = submit_answers(self.conn, LEARNER, qid, self.answers_for(qid, wrong))
                self.assertEqual((result['graded']['score'], result['graded']['passed']), (score, passed))
                self.assertEqual((result['status'], result['next_step']), ('grading', 'assessment_feedback'))
                self.assertEqual(self.conn.execute('SELECT score, passed FROM results WHERE result_id = ?',
                                                  (result['result_id'],)).fetchone(), (score, int(passed)))
                self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM responses WHERE attempt_id = ?',
                                                  (result['attempt_id'],)).fetchone()[0], 10)
                counts = dict(self.conn.execute('SELECT concept_id, missed_count FROM weak_concepts WHERE source_attempt_id = ?',
                                                (result['attempt_id'],)))
                self.assertEqual(counts, {w['concept_id']: w['missed_count'] for w in result['graded']['weak_concepts']})
                if score == 6:
                    self.assertEqual(counts, {'c_fatty_acid_saturation': 2, 'c_cholesterol_buffer': 2})
                self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions WHERE session_id = ?',
                                                  (result['session_id'],)).fetchone()[0], 0)

    def test_incomplete_unknown_or_invalid_answers_create_no_attempt(self):
        started = self.start_quiz()
        qid = started['quiz']['quiz_id']
        answers = self.answers_for(qid)
        missing = dict(answers); missing.pop(next(iter(missing)))
        unknown = dict(answers); unknown['q_unknown'] = 'a'
        invalid = dict(answers); invalid[next(iter(invalid))] = 'z'
        for values in [missing, unknown, invalid, None]:
            with self.subTest(answers=type(values).__name__):
                with self.assertRaises(SubmissionRejected):
                    submit_answers(self.conn, LEARNER, qid, values)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'awaiting_answers')

    def test_other_learner_cannot_submit_quiz_or_see_key(self):
        started = self.start_quiz()
        qid = started['quiz']['quiz_id']
        with self.assertRaises(SubmissionRejected) as error:
            submit_answers(self.conn, 'learner_z1b2c3d4e5f6', qid, self.answers_for(qid))
        self.assertEqual(str(error.exception), 'Quiz is unavailable for this learner.')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)

    def test_replayed_submission_never_creates_second_attempt(self):
        started = self.start_quiz()
        qid = started['quiz']['quiz_id']
        answers = self.answers_for(qid)
        original = submit_answers(self.conn, LEARNER, qid, answers)
        before = list(self.conn.iterdump())
        self.assertEqual(submit_answers(self.conn, LEARNER, qid, answers), original)
        self.assertEqual(list(self.conn.iterdump()), before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM results').fetchone()[0], 1)

    def test_failed_result_save_rolls_back_attempt_responses_and_weak_concepts(self):
        started = self.start_quiz()
        qid = started['quiz']['quiz_id']
        self.conn.execute("CREATE TRIGGER fail_result BEFORE INSERT ON results BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        result = submit_answers(self.conn, LEARNER, qid, self.answers_for(qid, (3, 4, 6, 7)))
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['graded'])
        for table in ['attempts', 'responses', 'results', 'weak_concepts']:
            self.assertEqual(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'failed')

    def test_missing_private_key_is_a_technical_failure_not_a_wrong_answer(self):
        started = self.start_quiz()
        qid = started['quiz']['quiz_id']
        answers = self.answers_for(qid)
        self.conn.execute('DELETE FROM answer_keys WHERE question_id = ?', (next(iter(answers)),))
        self.conn.commit()
        result = submit_answers(self.conn, LEARNER, qid, answers)
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['graded'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)

    def test_simultaneous_submissions_save_only_one_attempt(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import closing
        import tempfile
        import threading
        started = self.start_quiz()
        qid = started['quiz']['quiz_id']
        answers = self.answers_for(qid)
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'study.sqlite')
            disk = sqlite3.connect(path)
            self.conn.backup(disk)
            disk.close()
            barrier = threading.Barrier(2)
            def submit():
                conn = sqlite3.connect(path, timeout=5)
                conn.execute('PRAGMA foreign_keys = ON')
                try:
                    barrier.wait(timeout=5)
                    try:
                        return submit_answers(conn, LEARNER, qid, answers)['status']
                    except SubmissionRejected:
                        return 'rejected'
                finally:
                    conn.close()
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: submit(), range(2)))
            self.assertCountEqual(outcomes, ['grading', 'grading'])
            with closing(sqlite3.connect(path)) as saved:
                self.assertEqual(saved.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
                self.assertEqual(saved.execute('SELECT COUNT(*) FROM results').fetchone()[0], 1)
                self.assertEqual(saved.execute('SELECT COUNT(*) FROM responses').fetchone()[0], 10)


    def test_changed_replay_rejected_without_altering_saved_evidence(self):
        started = self.start_quiz(); qid = started['quiz']['quiz_id']
        answers = self.answers_for(qid)
        submit_answers(self.conn, LEARNER, qid, answers)
        before = list(self.conn.iterdump())
        with self.assertRaisesRegex(SubmissionRejected, 'different answers'):
            submit_answers(self.conn, LEARNER, qid, self.answers_for(qid, (1,)))
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_replay_does_not_regrade_or_rewind_later_session_state(self):
        from unittest.mock import patch
        started = self.start_quiz(); qid = started['quiz']['quiz_id']
        answers = self.answers_for(qid)
        original = submit_answers(self.conn, LEARNER, qid, answers)
        self.conn.execute("UPDATE study_sessions SET status='failed' WHERE session_id=?", (original['session_id'],))
        self.conn.commit()
        before = list(self.conn.iterdump())
        with patch('study_buddy.controller.grade', side_effect=AssertionError('Replay must not regrade')):
            replay = submit_answers(self.conn, LEARNER, qid, answers)
        self.assertEqual(replay['attempt_id'], original['attempt_id'])
        self.assertEqual(replay['graded'], original['graded'])
        self.assertEqual(replay['status'], 'failed')
        self.assertIsNone(replay['next_step'])
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_simultaneous_different_answers_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import closing
        import tempfile
        import threading
        started = self.start_quiz(); qid = started['quiz']['quiz_id']
        submissions = [self.answers_for(qid), self.answers_for(qid, (1,))]
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'study.db')
            with closing(sqlite3.connect(path)) as target:
                self.conn.backup(target)
            barrier = threading.Barrier(2)
            def submit(answers):
                with closing(sqlite3.connect(path, timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON')
                    barrier.wait(timeout=5)
                    try:
                        return submit_answers(conn, LEARNER, qid, answers)
                    except SubmissionRejected:
                        return None
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(submit, submissions))
            self.assertEqual(sum(result is not None for result in outcomes), 1)
            winner = next(result for result in outcomes if result is not None)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM results').fetchone()[0], 1)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM responses').fetchone()[0], 10)
                self.assertEqual(conn.execute('SELECT score FROM results').fetchone()[0], winner['graded']['score'])
