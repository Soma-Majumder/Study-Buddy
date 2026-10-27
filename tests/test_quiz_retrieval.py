"""Ownership, publication integrity and snapshot-only learner quiz retrieval."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tests import test_quality_publication as fixtures
from study_buddy.controller import get_current_quiz, QuizRetrievalRejected, submit_answers
from study_buddy.answer_keys import get_answer_key

LEARNER = fixtures.LEARNER


class QuizRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.QualityPublicationTests(); self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn = self.helper.conn
        self.helper.approve(); self.published = self.helper.publish()
        self.sid = self.published['session_id']

    def get(self):
        return get_current_quiz(self.conn, LEARNER, self.sid)

    def test_returns_same_ten_question_projection_without_writing(self):
        before = list(self.conn.iterdump()); changes = self.conn.total_changes
        first = self.get(); second = self.get()
        self.assertEqual(first, second)
        self.assertEqual(first['quiz'], self.published['quiz'])
        self.assertEqual(first['next_step'], 'submit_answers')
        self.assertEqual(len(first['quiz']['questions']), 10)
        for question in first['quiz']['questions']:
            self.assertEqual(set(question), {'question_id','position','prompt','options'})
            self.assertEqual(set(question['options']), set('abcd'))
        self.assertEqual(list(self.conn.iterdump()), before)
        self.assertEqual(self.conn.total_changes, changes)
        self.assertFalse(self.conn.in_transaction)

    def test_ownership_is_checked_before_private_evidence_is_read(self):
        def authorize(action, table, column, database, source):
            if action == sqlite3.SQLITE_READ and table != 'study_sessions':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.conn.set_authorizer(authorize)
        try:
            for learner, sid in [('other',self.sid),(LEARNER,'missing')]:
                with self.assertRaisesRegex(QuizRetrievalRejected, 'unavailable'):
                    get_current_quiz(self.conn, learner, sid)
        finally:
            self.conn.set_authorizer(None)

    def test_nonawaiting_states_are_rejected_without_changes(self):
        for status in ('started','teaching','grading','reteaching','pending_save','completed','failed'):
            self.conn.execute('UPDATE study_sessions SET status=?', (status,));self.conn.commit()
            before = list(self.conn.iterdump())
            with self.subTest(status=status), self.assertRaises(QuizRetrievalRejected):self.get()
            self.assertEqual(list(self.conn.iterdump()), before)

    def test_missing_publication_or_invalid_report_blocks_retrieval(self):
        for sql in ('DELETE FROM quiz_quality_publications', "UPDATE quiz_quality_reports SET report_data='{}'"):
            self.conn.execute('SAVEPOINT tamper')
            self.conn.execute(sql)
            with self.subTest(sql=sql), self.assertRaises(QuizRetrievalRejected):self.get()
            self.conn.execute('ROLLBACK TO tamper');self.conn.execute('RELEASE tamper')

    def test_modified_questions_keys_or_approval_binding_are_rejected(self):
        for sql in ("UPDATE questions SET prompt='changed' WHERE position=1",
                    "UPDATE answer_keys SET correct_option=CASE correct_option WHEN 'a' THEN 'b' ELSE 'a' END",
                    "UPDATE quiz_quality_publications SET candidate_hash='changed'",
                    "UPDATE quiz_quality_operations SET stage='failed'",
                    "UPDATE lessons SET approved=0"):
            self.conn.execute('SAVEPOINT tamper');self.conn.execute(sql)
            with self.subTest(sql=sql), self.assertRaises(QuizRetrievalRejected):self.get()
            self.conn.execute('ROLLBACK TO tamper');self.conn.execute('RELEASE tamper')

    def test_newer_unpublished_quiz_never_falls_back_to_old_publication(self):
        quiz = self.conn.execute('SELECT explanation_id,round FROM quizzes').fetchone()
        self.conn.execute('INSERT INTO quizzes VALUES (?,?,?,?)', ('quiz_newer',self.sid,*quiz));self.conn.commit()
        with self.assertRaises(QuizRetrievalRejected):self.get()

    def test_submitted_quiz_is_rejected_even_with_stale_awaiting_status(self):
        qid=self.published['quiz']['quiz_id']
        answers={key['question_id']:key['correct_option'] for key in get_answer_key(self.conn,qid)}
        submit_answers(self.conn,LEARNER,qid,answers)
        self.conn.execute("UPDATE study_sessions SET status='awaiting_answers'");self.conn.commit()
        with self.assertRaises(QuizRetrievalRejected):self.get()

    def test_published_quiz_survives_preparation_deadline(self):
        self.conn.execute('UPDATE quiz_quality_operations SET started_at=1,deadline_at=2');self.conn.commit()
        self.assertEqual(self.get()['quiz'],self.published['quiz'])

    def test_readonly_connection_and_caller_transaction_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'quiz.db'
            with closing(sqlite3.connect(path)) as target:self.conn.backup(target)
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as readonly:
                self.assertEqual(get_current_quiz(readonly,LEARNER,self.sid),self.get())
                self.assertFalse(readonly.in_transaction)
        self.conn.execute("INSERT INTO learners VALUES ('uncommitted')")
        self.get(); self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertIsNone(self.conn.execute("SELECT 1 FROM learners WHERE learner_id='uncommitted'").fetchone())

    def test_retrieval_executes_no_application_writes(self):
        def authorize(action, first, second, database, source):
            if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.conn.set_authorizer(authorize)
        try:self.assertEqual(self.get()['quiz'],self.published['quiz'])
        finally:self.conn.set_authorizer(None)

    def test_focused_retry_is_retrievable_without_new_calls_or_retry_credit(self):
        from tests import test_retry_quiz as retry_fixtures
        helper=retry_fixtures.RetryQuizTests();helper.setUp();self.addCleanup(helper.doCleanups)
        ready=helper.dispatch()
        before=list(helper.conn.iterdump())
        result=get_current_quiz(helper.conn,LEARNER,ready['session_id'])
        self.assertEqual(result['quiz'],ready['quiz'])
        self.assertEqual(list(helper.conn.iterdump()),before)
        self.assertEqual(helper.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0],1)
