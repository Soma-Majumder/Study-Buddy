"""Completed summary reads: owner isolation, filtered fields and no writes."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests import test_summary as fixtures
from study_buddy.controller import get_saved_summary, SummaryRetrievalRejected

LEARNER=fixtures.LEARNER


class SummaryRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.SummaryTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.conn=self.helper.conn;self.sid=self.helper.sid
        self.saved=self.helper.finish()

    def get(self):return get_saved_summary(self.conn,LEARNER,self.sid)

    def store(self,data):
        self.conn.execute('UPDATE session_summaries SET summary=? WHERE session_id=?',(json.dumps(data),self.sid));self.conn.commit()

    def test_repeat_reads_match_saved_summary_without_writes_or_completion(self):
        before=list(self.conn.iterdump());changes=self.conn.total_changes
        with patch('study_buddy.controller.finish_session',side_effect=AssertionError('No completion on read')):
            self.assertEqual(self.get(),self.saved)
            self.assertEqual(self.get(),self.saved)
        self.assertEqual(list(self.conn.iterdump()),before)
        self.assertEqual(self.conn.total_changes,changes)
        self.assertFalse(self.conn.in_transaction)

    def test_unknown_extra_fields_are_omitted_at_every_level(self):
        data=copy.deepcopy(self.saved['summary'])
        for target in (data,data['lesson'],data['attempts'][0],data['attempts'][0]['weak_concepts'][0],data['concepts_to_revisit'][0]):
            target['private_test_field']='fixture-only'
        data['message']='unexpected freeform content'
        self.store(data)
        output=self.get()
        self.assertNotIn('private_test_field',json.dumps(output))
        self.assertNotIn('unexpected freeform',json.dumps(output))
        self.assertEqual(output,self.saved)

    def test_unknown_session_wrong_owner_and_not_completed_are_rejected(self):
        messages=[]
        for learner,sid in [('other',self.sid),(LEARNER,'missing')]:
            with self.assertRaises(SummaryRetrievalRejected) as error:get_saved_summary(self.conn,learner,sid)
            messages.append(str(error.exception))
        for status in ('teaching','awaiting_answers','grading','reteaching','pending_save','failed'):
            self.conn.execute('UPDATE study_sessions SET status=?',(status,));self.conn.commit()
            with self.assertRaises(SummaryRetrievalRejected) as error:self.get()
            messages.append(str(error.exception))
        self.assertEqual(len(set(messages)),1)

    def test_missing_or_malformed_summary_fails_without_claiming_completion(self):
        for raw in ('{','[]','null'):
            self.conn.execute('UPDATE session_summaries SET summary=?',(raw,));self.conn.commit()
            with self.subTest(raw=raw),self.assertRaises(SummaryRetrievalRejected):self.get()
        self.conn.execute('DELETE FROM session_summaries');self.conn.commit()
        with self.assertRaises(SummaryRetrievalRejected):self.get()

    def test_tampered_identity_results_or_concepts_are_rejected(self):
        for kind in ('session','lesson','score','attempt','revisit','count'):
            data=copy.deepcopy(self.saved['summary'])
            if kind=='session':data['session_id']='other'
            elif kind=='lesson':data['lesson']['version']=99
            elif kind=='score':data['attempts'][0]['score']=10
            elif kind=='attempt':data['attempts'][0]['attempt_id']='other'
            elif kind=='revisit':data['concepts_to_revisit']=[]
            else:data['learning_retry_count']=True
            self.store(data)
            with self.subTest(kind=kind),self.assertRaises(SummaryRetrievalRejected):self.get()

    def test_readonly_connection_and_caller_transaction_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'summary.db'
            with closing(sqlite3.connect(path)) as target:self.conn.backup(target)
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as readonly:
                self.assertEqual(get_saved_summary(readonly,LEARNER,self.sid),self.saved)
                self.assertFalse(readonly.in_transaction)
        self.conn.execute("INSERT INTO learners VALUES ('uncommitted')")
        self.get();self.assertTrue(self.conn.in_transaction)
        with self.assertRaises(SummaryRetrievalRejected):get_saved_summary(self.conn,'other',self.sid)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertIsNone(self.conn.execute("SELECT 1 FROM learners WHERE learner_id='uncommitted'").fetchone())

    def test_no_writes_or_private_answer_key_reads(self):
        def authorize(action,first,second,database,source):
            if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):return sqlite3.SQLITE_DENY
            if action==sqlite3.SQLITE_READ and first in ('answer_keys','quiz_quality_candidate_keys','quiz_quality_reports'):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.conn.set_authorizer(authorize)
        try:self.assertEqual(self.get(),self.saved)
        finally:self.conn.set_authorizer(None)

    def test_completed_retry_summary_preserves_both_scores(self):
        from tests import test_session_flow as flow_fixtures
        helper=flow_fixtures.SessionFlowTests();helper.setUp();self.addCleanup(helper.doCleanups)
        done=helper.run_flow(helper.learner({3,4,6,7},{1,2,3}))
        self.assertEqual(done['status'],'completed')
        result=get_saved_summary(helper.conn,LEARNER,done['session_id'])
        self.assertEqual([attempt['score'] for attempt in result['summary']['attempts']],[6,7])
        self.assertEqual(result['summary']['learning_retry_count'],1)
        self.assertEqual(result['summary'],done['summary'])
