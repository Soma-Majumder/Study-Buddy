"""Completion only after a durable summary, using offline agent fixtures."""
import copy
import json
from pathlib import Path
import sqlite3
import unittest

from tests import test_routing as routing_fixtures
from tests import test_retry_quiz as retry_fixtures
from study_buddy.controller import finish_session, decide_next_step, submit_answers, explain_results
from study_buddy.summary import SummaryRejected
from study_buddy.answer_keys import get_answer_key

ROOT = Path(__file__).resolve().parent.parent
LEARNER = routing_fixtures.LEARNER


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = routing_fixtures.RoutingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.conn = self.fixture.conn
        self.result = self.fixture.prepare((1, 2))
        self.sid = self.result['session_id']
        decide_next_step(self.conn, LEARNER, self.result['attempt_id'])

    def finish(self):
        return finish_session(self.conn, LEARNER, self.sid)

    def test_pass_summary_is_saved_before_completion_and_contains_no_keys(self):
        self.conn.execute("CREATE TRIGGER require_summary BEFORE UPDATE ON study_sessions WHEN NEW.status = 'completed' AND NOT EXISTS (SELECT 1 FROM session_summaries WHERE session_id = NEW.session_id) BEGIN SELECT RAISE(ABORT, 'summary missing'); END")
        result = self.finish()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['summary']['attempts'][0]['score'], 8)
        self.assertEqual(result['summary']['learning_retry_count'], 0)
        self.assertEqual(result['summary']['lesson']['version'], 2)
        for field in ['answer_key', 'correct_option', 'rationale']:
            self.assertNotIn(field, json.dumps(result))
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'completed')
        self.assertEqual(json.loads(self.conn.execute('SELECT summary FROM session_summaries').fetchone()[0]), result['summary'])

    def test_repeated_completion_returns_same_summary(self):
        self.assertEqual(self.finish(), self.finish())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM session_summaries').fetchone()[0], 1)

    def test_summary_save_failure_keeps_pending_status_and_can_be_retried(self):
        self.conn.execute("CREATE TRIGGER fail_summary BEFORE INSERT ON session_summaries BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.assertEqual(self.finish()['status'], 'pending_save')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM session_summaries').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM results').fetchone()[0], 1)
        self.conn.execute('DROP TRIGGER fail_summary')
        self.assertEqual(self.finish()['status'], 'completed')

    def test_failed_completion_update_rolls_back_summary_too(self):
        self.conn.execute("CREATE TRIGGER fail_completed BEFORE UPDATE ON study_sessions WHEN NEW.status = 'completed' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.assertEqual(self.finish()['status'], 'pending_save')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM session_summaries').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT status FROM session_routes').fetchone()[0], 'summary_pending')

    def test_wrong_learner_and_premature_completion_are_rejected(self):
        with self.assertRaises(SummaryRejected): finish_session(self.conn, 'learner_z1b2c3d4e5f6', self.sid)
        self.conn.execute("UPDATE study_sessions SET status = 'grading'")
        self.conn.commit()
        with self.assertRaises(SummaryRejected): self.finish()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM session_summaries').fetchone()[0], 0)

    def test_inconsistent_saved_score_is_not_summarized_as_valid(self):
        self.conn.execute('UPDATE results SET score = 10')
        self.conn.commit()
        with self.assertRaises(SummaryRejected): self.finish()
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'pending_save')

    def test_full_retry_path_finishes_with_two_results_even_after_low_retry_score(self):
        helper = retry_fixtures.RetryQuizTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        ready = helper.dispatch()
        conn = helper.conn
        qid = ready['quiz']['quiz_id']
        keys = get_answer_key(conn, qid)
        result = submit_answers(conn, LEARNER, qid,
                                {k['question_id']: 'b' if k['correct_option'] == 'a' else 'a' for k in keys})
        def feedback(c, *, repair_request=None):
            return dict(schema_version='1.0', quiz_id=c['quiz_id'], attempt_id=c['attempt_id'],
                        feedback=[dict(position=i['position'], explanation=i['rationale']) for i in c['graded']['items']],
                        weak_concepts=copy.deepcopy(c['graded']['weak_concepts']), summary='Practice result.')
        explain_results(conn, LEARNER, result['attempt_id'], assessment=feedback)
        decide_next_step(conn, LEARNER, result['attempt_id'])
        final = finish_session(conn, LEARNER, result['session_id'])
        self.assertEqual(final['status'], 'completed')
        self.assertEqual([(a['round'], a['score']) for a in final['summary']['attempts']], [('first', 6), ('retry', 0)])
        self.assertEqual(final['summary']['learning_retry_count'], 1)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 2)

    def test_simultaneous_completion_saves_one_summary(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import closing
        import tempfile
        import threading
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'summary.sqlite')
            with closing(sqlite3.connect(path)) as disk: self.conn.backup(disk)
            barrier = threading.Barrier(2)
            def finish(_):
                with closing(sqlite3.connect(path, timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys = ON')
                    barrier.wait(timeout=5)
                    return finish_session(conn, LEARNER, self.sid)
            with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(finish, range(2)))
            self.assertEqual(results[0], results[1])
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM session_summaries').fetchone()[0], 1)

    def test_additive_summary_migration_preserves_prior_results(self):
        self.conn.execute('DROP TABLE session_summaries')
        self.conn.executescript((ROOT / 'db/migrations/004_session_summaries.sql').read_text())
        self.assertEqual(self.finish()['status'], 'completed')
