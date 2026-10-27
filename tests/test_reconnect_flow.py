"""Offline reconnect integration: reopen real SQLite between every read/action."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tests import test_session_flow as fixtures
from tests.quality_fixtures import passing_review
from study_buddy.answer_keys import get_answer_key
from study_buddy.controller import (start_quiz_session, submit_answers, explain_results,
    decide_next_step, dispatch_reteaching, dispatch_retry_quiz, finish_session,
    get_session_status, get_current_quiz, get_saved_summary,
    SessionStatusRejected, QuizRetrievalRejected, SummaryRetrievalRejected)

LEARNER=fixtures.LEARNER


class ReconnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.SessionFlowTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'reconnect.db'
        with closing(sqlite3.connect(self.path)) as disk:self.helper.conn.backup(disk)
        self.sid=None

    def connect(self):
        conn=sqlite3.connect(self.path);conn.execute('PRAGMA foreign_keys=ON');return conn

    def start(self):
        with closing(self.connect()) as conn:
            started=start_quiz_session(conn,LEARNER,'lesson_cell_membranes',tutor=self.helper.tutor,
                assessment=self.helper.assessment,reviewer=passing_review)
        self.sid=started['session_id'];return started['quiz']

    def submit(self,quiz,wrong):
        with closing(self.connect()) as conn:
            keys=get_answer_key(conn,quiz['quiz_id'])
            answers={key['question_id']:(('b' if key['correct_option']=='a' else 'a') if pos in wrong else key['correct_option'])
                     for pos,key in enumerate(keys,1)}
            return submit_answers(conn,LEARNER,quiz['quiz_id'],answers)

    def route(self,result):
        with closing(self.connect()) as conn:
            explain_results(conn,LEARNER,result['attempt_id'],assessment=self.helper.feedback)
            return decide_next_step(conn,LEARNER,result['attempt_id'])

    def reconnect(self,expected,quiz=None,scores=None):
        # Every reconnect uses a new read-only connection: no saved Python state
        # or write privileges are needed to recover learner-visible progress.
        calls=(len(self.helper.tutor_calls),len(self.helper.assessment_calls),len(self.helper.feedback_calls))
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as conn:
            before=list(conn.iterdump())
            status=get_session_status(conn,LEARNER,self.sid)
            self.assertEqual(status['status'],expected)
            self.assertEqual(status['quiz_available'],quiz is not None)
            self.assertEqual(status['summary_available'],scores is not None)
            if quiz is not None:
                returned=get_current_quiz(conn,LEARNER,self.sid)
                self.assertEqual(returned['quiz'],quiz)
                for question in returned['quiz']['questions']:
                    self.assertEqual(set(question),{'question_id','position','prompt','options'})
            else:
                with self.assertRaises(QuizRetrievalRejected):get_current_quiz(conn,LEARNER,self.sid)
            if scores is not None:
                summary=get_saved_summary(conn,LEARNER,self.sid)
                self.assertEqual([a['score'] for a in summary['summary']['attempts']],scores)
                self.assertEqual(get_saved_summary(conn,LEARNER,self.sid),summary)
            else:
                with self.assertRaises(SummaryRetrievalRejected):get_saved_summary(conn,LEARNER,self.sid)
            for action,rejected in ((get_session_status,SessionStatusRejected),(get_current_quiz,QuizRetrievalRejected),(get_saved_summary,SummaryRetrievalRejected)):
                with self.assertRaises(rejected):action(conn,'learner_other',self.sid)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(list(conn.iterdump()),before)
        self.assertEqual((len(self.helper.tutor_calls),len(self.helper.assessment_calls),len(self.helper.feedback_calls)),calls)

    def test_pass_then_reconnect_at_quiz_grading_pending_and_completed(self):
        quiz=self.start();self.reconnect('awaiting_answers',quiz=quiz)
        self.reconnect('awaiting_answers',quiz=quiz)
        result=self.submit(quiz,{1,2});self.reconnect('grading')
        self.assertEqual(self.route(result)['next_step'],'summary');self.reconnect('pending_save')
        with closing(self.connect()) as conn:finished=finish_session(conn,LEARNER,self.sid)
        self.assertEqual(finished['status'],'completed');self.reconnect('completed',scores=[8])
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],1)
        self.assertEqual((len(self.helper.tutor_calls),len(self.helper.assessment_calls),len(self.helper.feedback_calls)),(1,1,1))

    def test_focused_retry_reconnect_never_exposes_old_quiz(self):
        first=self.start();result=self.submit(first,{3,4,6,7})
        self.assertEqual(self.route(result)['next_step'],'reteach');self.reconnect('reteaching')
        with closing(self.connect()) as conn:
            dispatch_reteaching(conn,LEARNER,result['attempt_id'],tutor=self.helper.tutor)
            retry=dispatch_retry_quiz(
                conn,LEARNER,result['attempt_id'],assessment=self.helper.assessment,reviewer=passing_review)
        quiz=retry['quiz'];self.assertNotEqual(quiz['quiz_id'],first['quiz_id'])
        self.reconnect('awaiting_answers',quiz=quiz)
        second=self.submit(quiz,{1,2,3});self.reconnect('grading')
        self.assertEqual(self.route(second)['next_step'],'summary')
        with closing(self.connect()) as conn:finish_session(conn,LEARNER,self.sid)
        self.reconnect('completed',scores=[6,7])
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],2)

    def test_reconnect_after_failed_summary_save_preserves_results_and_allows_later_read(self):
        quiz=self.start();result=self.submit(quiz,set());self.route(result)
        with closing(self.connect()) as conn:
            conn.execute("CREATE TRIGGER fail_summary BEFORE INSERT ON session_summaries BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
            conn.commit();failed=finish_session(conn,LEARNER,self.sid)
        self.assertEqual(failed['status'],'pending_save');self.reconnect('pending_save')
        calls=(len(self.helper.tutor_calls),len(self.helper.assessment_calls),len(self.helper.feedback_calls))
        with closing(self.connect()) as conn:
            conn.execute('DROP TRIGGER fail_summary');conn.commit()
            self.assertEqual(finish_session(conn,LEARNER,self.sid)['status'],'completed')
        self.reconnect('completed',scores=[10])
        self.assertEqual((len(self.helper.tutor_calls),len(self.helper.assessment_calls),len(self.helper.feedback_calls)),calls)
