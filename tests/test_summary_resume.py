"""Resume final saving using durable session IDs, without any agent dispatch."""
from contextlib import closing, ExitStack
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests import test_session_flow as fixtures
from study_buddy.session_flow import resume_summary
from study_buddy.summary import SummaryRejected

LEARNER = fixtures.LEARNER


class SummaryResumeTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.SessionFlowTests(); self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn = self.helper.conn
        self.conn.execute("CREATE TRIGGER fail_summary BEFORE INSERT ON session_summaries BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.conn.commit()
        self.stopped = self.helper.run_flow(self.helper.learner({1, 2}))
        self.sid = self.stopped['session_id']
        self.assertEqual(self.stopped['status'], 'pending_save')
        self.assertEqual(self.stopped['next_action'], 'resume_summary')

    def evidence(self, conn):
        return {table: conn.execute(f'SELECT * FROM {table}').fetchall() for table in
                ('quizzes', 'questions', 'attempts', 'responses', 'results', 'weak_concepts',
                 'feedback_operations', 'handoff_repairs', 'quiz_quality_calls')}

    def no_agents(self):
        stack = ExitStack()
        for name in ('start_quiz_session', 'submit_answers', 'explain_results', 'dispatch_reteaching',
                     'dispatch_retry_quiz', 'run_tutor', 'run_assessment', 'run_feedback'):
            stack.enter_context(patch('study_buddy.session_flow.' + name,
                side_effect=AssertionError('Resume must not dispatch ' + name)))
        return stack

    def test_resume_succeeds_without_new_learning_work_and_replay_is_idempotent(self):
        before = self.evidence(self.conn)
        self.conn.execute('DROP TRIGGER fail_summary'); self.conn.commit()
        with self.no_agents():
            resumed = resume_summary(self.conn, LEARNER, self.sid)
            replay = resume_summary(self.conn, LEARNER, self.sid)
        self.assertEqual(resumed, replay)
        self.assertEqual((resumed['status'], resumed['scores']), ('completed', [8]))
        self.assertEqual([step['step'] for step in resumed['steps']], ['summary'])
        self.assertEqual(before, self.evidence(self.conn))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM session_summaries').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)

    def test_repeated_save_failure_stays_pending_with_saved_scores(self):
        before = self.evidence(self.conn)
        with self.no_agents():
            resumed = resume_summary(self.conn, LEARNER, self.sid)
        self.assertEqual((resumed['status'], resumed['scores']), ('pending_save', [8]))
        self.assertEqual(resumed['next_action'], 'resume_summary')
        self.assertIsNone(resumed['summary'])
        self.assertIsNotNone(resumed['error'])
        self.assertEqual(before, self.evidence(self.conn))

    def test_other_learner_unknown_and_premature_sessions_are_rejected(self):
        before = list(self.conn.iterdump())
        for learner, sid in [('learner_other', self.sid), (LEARNER, 'session_missing')]:
            with self.assertRaises(SummaryRejected):
                resume_summary(self.conn, learner, sid)
        self.assertEqual(before, list(self.conn.iterdump()))
        self.conn.execute("UPDATE study_sessions SET status='grading'"); self.conn.commit()
        with self.assertRaises(SummaryRejected):
            resume_summary(self.conn, LEARNER, self.sid)

    def test_resume_after_process_connection_is_reopened(self):
        self.conn.execute('DROP TRIGGER fail_summary'); self.conn.commit()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'resume.db'
            with closing(sqlite3.connect(path)) as target:
                self.conn.backup(target)
            with closing(sqlite3.connect(path)) as reopened, self.no_agents():
                reopened.execute('PRAGMA foreign_keys=ON')
                result = resume_summary(reopened, LEARNER, self.sid)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(result['session_id'], self.sid)
                self.assertEqual(result['scores'], [8])
