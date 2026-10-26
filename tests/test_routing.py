"""Offline retry routing with stored Tutor/quiz/feedback fixtures and real grading."""
from tests.quality_fixtures import passing_review
import copy
import json
from pathlib import Path
import sqlite3
import unittest

from study_buddy.controller import start_quiz_session, submit_answers, explain_results, decide_next_step
from study_buddy.routing import RoutingRejected
from study_buddy.answer_keys import get_answer_key
from study_buddy.lessons import load_lessons

ROOT = Path(__file__).resolve().parent.parent
LEARNER = 'learner_a1b2c3d4e5f6'


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.executescript((ROOT / 'db/schema.sql').read_text())
        load_lessons(self.conn)
        self.conn.execute('INSERT INTO learners VALUES (?)', (LEARNER,))
        self.conn.commit()

    def prepare(self, missed=(3, 4, 6, 7), feedback=True):
        def tutor(context):
            h = json.loads((ROOT / 'schemas/v1/examples/tutor_to_assessment.example.json').read_text())
            h['handoff_id'] = context['handoffId']
            h['context']['learner_session_id'] = context['sessionId']
            h['payload']['explanation_id'] = context['explanationId']
            return dict(handoff=h, repairCount=0)
        def assessment(h, *, repair_request=None, **timeouts):
            q = json.loads((ROOT / 'schemas/v1/examples/quiz_output.example.json').read_text())
            q.update(handoff_id=h['handoff_id'], explanation_id=h['payload']['explanation_id'])
            return q
        started = start_quiz_session(self.conn, LEARNER, 'lesson_cell_membranes', tutor=tutor, assessment=assessment, reviewer=passing_review)
        qid = started['quiz']['quiz_id']
        keys = get_answer_key(self.conn, qid)
        answers = {k['question_id']: ('b' if k['correct_option'] == 'a' else 'a') if i in missed else k['correct_option']
                   for i, k in enumerate(keys, 1)}
        result = submit_answers(self.conn, LEARNER, qid, answers)
        if feedback:
            def explain(c, *, repair_request=None, **timeouts):
                return dict(schema_version='1.0', quiz_id=c['quiz_id'], attempt_id=c['attempt_id'],
                            feedback=[dict(position=i['position'], explanation=i['rationale']) for i in c['graded']['items']],
                            weak_concepts=copy.deepcopy(c['graded']['weak_concepts']), summary='Review your results.')
            explain_results(self.conn, LEARNER, result['attempt_id'], assessment=explain)
        return result

    def test_low_score_reserves_retry_and_prepares_private_key_free_return_handoff(self):
        result = self.prepare()
        route = decide_next_step(self.conn, LEARNER, result['attempt_id'])
        self.assertEqual((route['next_step'], route['operation_status'], route['learning_retry_count']), ('reteach', 'reserved', 1))
        self.assertEqual(route['return_handoff']['payload']['score'], 6)
        self.assertEqual(route['current_attempt']['missedConceptCounts'], {'c_fatty_acid_saturation': 2, 'c_cholesterol_buffer': 2})
        self.assertNotIn('correct_option', json.dumps(route))
        self.assertNotIn('answer_key', json.dumps(route))
        self.assertEqual(self.conn.execute('SELECT status, learning_retry_count FROM study_sessions').fetchone(), ('reteaching', 1))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 1)

    def test_seven_reserves_retry_and_eight_moves_to_summary_without_completion(self):
        for missed, score, expected in [((1, 2, 3), 7, 'reteach'), ((1, 2), 8, 'summary')]:
            with self.subTest(score=score):
                result = self.prepare(missed)
                route = decide_next_step(self.conn, LEARNER, result['attempt_id'])
                self.assertEqual(route['next_step'], expected)
                if score == 8:
                    self.assertEqual((route['status'], route['learning_retry_count']), ('pending_save', 0))

    def test_replayed_decision_returns_same_reservation(self):
        result = self.prepare()
        first = decide_next_step(self.conn, LEARNER, result['attempt_id'])
        self.assertEqual(first, decide_next_step(self.conn, LEARNER, result['attempt_id']))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM session_routes').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)

    def test_retry_round_goes_to_summary_even_after_another_low_score(self):
        result = self.prepare()
        self.conn.execute("UPDATE quizzes SET round = 'retry'")
        self.conn.execute('UPDATE study_sessions SET learning_retry_count = 1')
        self.conn.commit()
        route = decide_next_step(self.conn, LEARNER, result['attempt_id'])
        self.assertEqual((route['next_step'], route['reason']), ('summary', 'retry_finished'))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM session_routes WHERE route = 'reteach'").fetchone()[0], 0)

    def test_missing_validated_feedback_or_wrong_learner_blocks_routing(self):
        result = self.prepare(feedback=False)
        with self.assertRaises(RoutingRejected): decide_next_step(self.conn, LEARNER, result['attempt_id'])
        with self.assertRaises(RoutingRejected): decide_next_step(self.conn, 'learner_z1b2c3d4e5f6', result['attempt_id'])
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)

    def test_route_save_failure_rolls_back_reservation_and_state_change(self):
        result = self.prepare()
        before = self.conn.execute('SELECT COUNT(*) FROM handoff_repairs').fetchone()[0]
        self.conn.execute("CREATE TRIGGER fail_route BEFORE INSERT ON session_routes BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError): decide_next_step(self.conn, LEARNER, result['attempt_id'])
        self.assertEqual(self.conn.execute('SELECT status, learning_retry_count FROM study_sessions').fetchone(), ('grading', 0))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM handoff_repairs').fetchone()[0], before)

    def test_simultaneous_decisions_reserve_one_retry(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import closing
        import tempfile
        import threading
        result = self.prepare()
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'routing.sqlite')
            with closing(sqlite3.connect(path)) as disk:
                self.conn.backup(disk)
            barrier = threading.Barrier(2)
            def decide(_):
                with closing(sqlite3.connect(path, timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys = ON')
                    barrier.wait(timeout=5)
                    return decide_next_step(conn, LEARNER, result['attempt_id'])
            with ThreadPoolExecutor(max_workers=2) as pool:
                routes = list(pool.map(decide, range(2)))
            self.assertEqual(routes[0], routes[1])
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM session_routes').fetchone()[0], 1)
                self.assertEqual(conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)

    def test_older_attempt_cannot_trigger_a_route_after_newer_submission(self):
        result = self.prepare()
        self.conn.execute("INSERT INTO attempts VALUES ('attempt_newer', ?, ?, 'later')",
                          (result['quiz_id'], LEARNER))
        self.conn.commit()
        with self.assertRaises(RoutingRejected): decide_next_step(self.conn, LEARNER, result['attempt_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM session_routes').fetchone()[0], 0)

    def test_reserved_route_cannot_be_reused_after_session_fails(self):
        result = self.prepare()
        decide_next_step(self.conn, LEARNER, result['attempt_id'])
        self.conn.execute("UPDATE study_sessions SET status = 'failed'")
        self.conn.commit()
        with self.assertRaises(RoutingRejected): decide_next_step(self.conn, LEARNER, result['attempt_id'])
