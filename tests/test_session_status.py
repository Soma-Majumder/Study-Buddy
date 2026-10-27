"""Read-only status projections with real session and publication records."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from tests import test_controller as fixtures
from tests import test_summary as summary_fixtures
from study_buddy.controller import get_session_status, SessionStatusRejected, submit_answers

LEARNER = fixtures.LEARNER


class SessionStatusTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.ControllerTests(); self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn = self.helper.conn
        self.started = self.helper.start_quiz()
        self.sid = self.started['session_id']

    def status(self):
        return get_session_status(self.conn, LEARNER, self.sid)

    def test_published_quiz_status_has_only_allowlisted_fields_without_writes(self):
        before = list(self.conn.iterdump()); changes = self.conn.total_changes
        result = self.status()
        self.assertEqual(set(result), {'session_id','status','stage','learning_retry_count',
                                      'quiz_available','quiz_id','summary_available','message','next_action'})
        self.assertEqual(result['quiz_id'], self.started['quiz']['quiz_id'])
        self.assertTrue(result['quiz_available']); self.assertFalse(result['summary_available'])
        self.assertEqual(result['stage'], 'awaiting_answers')
        self.assertEqual(result['next_action'], 'get_current_quiz')
        self.assertEqual(list(self.conn.iterdump()), before)
        self.assertEqual(self.conn.total_changes, changes)

    def test_wrong_owner_unknown_session_and_invalid_ids_are_indistinguishable(self):
        messages = []
        for learner, sid in [('other', self.sid), (LEARNER, 'missing'), ('', self.sid), (LEARNER, None)]:
            with self.assertRaises(SessionStatusRejected) as error:
                get_session_status(self.conn, learner, sid)
            messages.append(str(error.exception))
        self.assertEqual(len(set(messages)), 1)

    def test_all_saved_states_are_reported_without_raw_failure_text(self):
        for status in ('started','teaching','awaiting_answers','grading','reteaching','pending_save','completed','failed'):
            self.conn.execute('UPDATE study_sessions SET status=?,failure_reason=? WHERE session_id=?',
                              (status, 'secret provider text or private key', self.sid))
            self.conn.commit()
            result = self.status()
            self.assertEqual(result['status'], status)
            self.assertNotIn('secret provider', json.dumps(result))
            if status != 'awaiting_answers':
                self.assertFalse(result['quiz_available']); self.assertIsNone(result['quiz_id'])
            if status == 'completed':
                self.assertFalse(result['summary_available']); self.assertIsNotNone(result['message'])

    def test_submitted_quiz_does_not_become_available_after_stale_status(self):
        qid = self.started['quiz']['quiz_id']
        submit_answers(self.conn, LEARNER, qid, self.helper.answers_for(qid))
        self.conn.execute("UPDATE study_sessions SET status='awaiting_answers'"); self.conn.commit()
        self.assertFalse(self.status()['quiz_available'])
        self.assertEqual(self.status()['next_action'], 'contact_support')

    def test_unpublished_quiz_cannot_be_advertised(self):
        self.conn.execute('DELETE FROM quiz_quality_publications'); self.conn.commit()
        self.assertFalse(self.status()['quiz_available'])
        self.assertEqual(self.status()['next_action'], 'contact_support')
        self.assertIsNone(self.status()['quiz_id'])

    def test_saved_summary_is_available_only_after_completion(self):
        helper = summary_fixtures.SummaryTests(); helper.setUp(); self.addCleanup(helper.doCleanups)
        pending = get_session_status(helper.conn, LEARNER, helper.sid)
        self.assertFalse(pending['summary_available'])
        self.assertEqual(pending['next_action'], 'resume_summary')
        helper.finish()
        result = get_session_status(helper.conn, LEARNER, helper.sid)
        self.assertTrue(result['summary_available']); self.assertFalse(result['quiz_available'])
        self.assertEqual(result['next_action'], 'get_saved_summary')

    def test_next_action_for_every_saved_state_is_advisory_and_does_not_write(self):
        expected = {
            'started': 'read_session_status', 'teaching': 'read_session_status',
            'grading': 'read_session_status', 'reteaching': 'read_session_status',
            'awaiting_answers': 'get_current_quiz', 'pending_save': 'resume_summary',
            'completed': 'contact_support', 'failed': 'start_new_session',
        }
        for status, action in expected.items():
            with self.subTest(status=status):
                self.conn.execute('UPDATE study_sessions SET status=? WHERE session_id=?', (status, self.sid))
                self.conn.commit()
                before = list(self.conn.iterdump())
                changes = self.conn.total_changes
                statements = []
                self.conn.set_trace_callback(statements.append)
                try:
                    result = self.status()
                finally:
                    self.conn.set_trace_callback(None)
                self.assertEqual(result['next_action'], action)
                self.assertEqual(self.conn.total_changes, changes)
                self.assertEqual(list(self.conn.iterdump()), before)
                self.assertEqual(len(statements), 1)
                self.assertTrue(statements[0].startswith('SELECT '))

    def test_read_only_database_connection_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status.db'
            with closing(sqlite3.connect(path)) as target:
                self.conn.backup(target)
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as readonly:
                self.assertEqual(get_session_status(readonly, LEARNER, self.sid), self.status())
                self.assertFalse(readonly.in_transaction)

    def test_caller_transaction_is_neither_committed_nor_rolled_back_and_keys_are_not_read(self):
        self.conn.execute("INSERT INTO learners VALUES ('uncommitted')")
        def authorize(action, first, second, database, source):
            if action == sqlite3.SQLITE_READ and first in ('answer_keys','quiz_quality_candidates','quiz_quality_reports','quiz_quality_candidate_keys'):
                return sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.conn.set_authorizer(authorize)
        try:
            self.assertTrue(self.status()['quiz_available'])
            self.assertTrue(self.conn.in_transaction)
        finally:
            self.conn.set_authorizer(None)
        self.conn.rollback()
        self.assertIsNone(self.conn.execute("SELECT 1 FROM learners WHERE learner_id='uncommitted'").fetchone())
