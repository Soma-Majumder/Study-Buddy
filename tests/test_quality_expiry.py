"""Deadline sweeps reconcile crash-left calls atomically, without model retries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from tests import test_quality_storage as fixtures
from tests import test_retry_quiz as retry_fixtures
from tests.quality_fixtures import passing_review
from study_buddy.quality_expiry import expire_quality_operation, sweep_expired_quality_operations
from study_buddy.quality_state import QualityStateRejected, reserve_quality_call, complete_quality_call
from study_buddy.quality_storage import store_candidate, store_review_report
from study_buddy.controller import (dispatch_quality_review, dispatch_quality_revision, continue_quality_quiz,
                                    publish_reviewed_quiz, dispatch_retry_quiz)

LEARNER=fixtures.LEARNER
ROOT=Path(__file__).resolve().parent.parent


class QualityExpiryTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.QualityStorageTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.conn,self.op=self.helper.conn,self.helper.op

    def deadline(self):
        return self.conn.execute('SELECT deadline_at FROM quiz_quality_operations WHERE operation_id=?',(self.op,)).fetchone()[0]

    def expire(self):
        self.conn.execute('UPDATE quiz_quality_operations SET started_at=?,deadline_at=? WHERE operation_id=?',
                          (time.time()-181,time.time()-1,self.op));self.conn.commit()

    def prepare(self,kind):
        if kind=='generation':return self.helper.call['call_id']
        if kind=='technical_repair':
            complete_quality_call(self.conn,LEARNER,self.helper.call['call_id'],outcome='invalid')
            return reserve_quality_call(self.conn,LEARNER,self.op,kind)['call_id']
        review=self.helper.prepare_review()
        if kind=='review':return review['call_id']
        report=self.helper.report()
        if kind=='approved':
            store_review_report(self.conn,LEARNER,review['call_id'],report);return review['call_id']
        report['quiz_review']['concept_coverage']='fail'
        report['quiz_review']['issues']=[dict(code='missing_coverage',detail='Repeated.',related_positions=[1,2])]
        store_review_report(self.conn,LEARNER,review['call_id'],report)
        revision=reserve_quality_call(self.conn,LEARNER,self.op,'revision')
        if kind=='revision':return revision['call_id']
        store_candidate(self.conn,LEARNER,revision['call_id'],self.helper.candidate)
        return reserve_quality_call(self.conn,LEARNER,self.op,'review_revision')['call_id']

    def test_expiry_boundary_and_idempotent_counts_without_budget_reset(self):
        call=self.prepare('generation');deadline=self.deadline()
        before=list(self.conn.iterdump())
        self.assertEqual(sweep_expired_quality_operations(self.conn,now=deadline-0.001)['operations_expired'],0)
        self.assertEqual(before,list(self.conn.iterdump()))
        budget=self.conn.execute('SELECT revision_count,review_count,deadline_at FROM quiz_quality_operations').fetchone()
        result=sweep_expired_quality_operations(self.conn,now=deadline)
        self.assertEqual(result,dict(operations_checked=1,operations_expired=1,calls_failed=1,sessions_failed=1,retry_operations_failed=0))
        self.assertEqual(self.conn.execute('SELECT status,outcome,finished_at FROM quiz_quality_calls WHERE call_id=?',(call,)).fetchone(),('failed','deadline',deadline))
        self.assertEqual(self.conn.execute('SELECT status,failure_reason,learning_retry_count FROM study_sessions').fetchone(),('failed','quality_deadline',0))
        self.assertEqual(self.conn.execute('SELECT revision_count,review_count,deadline_at FROM quiz_quality_operations').fetchone(),budget)
        saved=list(self.conn.iterdump())
        self.assertEqual(sweep_expired_quality_operations(self.conn,now=deadline+1)['operations_checked'],0)
        self.assertEqual(saved,list(self.conn.iterdump()))

    def test_all_crashed_call_kinds_fail_but_completed_evidence_is_preserved(self):
        for kind in ('review','revision','technical_repair','review_revision'):
            with self.subTest(kind=kind):
                helper=QualityExpiryTests();helper.setUp();self.addCleanup(helper.doCleanups)
                call=helper.prepare(kind)
                completed=helper.conn.execute("SELECT call_id,status,outcome,result_fingerprint FROM quiz_quality_calls WHERE status='completed'").fetchall()
                result=sweep_expired_quality_operations(helper.conn,now=helper.deadline())
                self.assertEqual(result['calls_failed'],1)
                self.assertEqual(helper.conn.execute('SELECT status,outcome FROM quiz_quality_calls WHERE call_id=?',(call,)).fetchone(),('failed','deadline'))
                self.assertEqual(helper.conn.execute("SELECT call_id,status,outcome,result_fingerprint FROM quiz_quality_calls WHERE status='completed'").fetchall(),completed)
                self.assertEqual(helper.conn.execute('SELECT stage,failure_code FROM quiz_quality_operations').fetchone(),('failed','quality_deadline'))

    def test_duplicate_review_revision_and_generation_entries_expire_without_dispatch(self):
        for kind,entry in [('generation',continue_quality_quiz),('review',dispatch_quality_review),('revision',dispatch_quality_revision)]:
            with self.subTest(kind=kind):
                helper=QualityExpiryTests();helper.setUp();self.addCleanup(helper.doCleanups)
                helper.prepare(kind);helper.expire()
                def forbidden(*args,**kwargs):self.fail('Expiry must not dispatch')
                kwargs={'reviewer':forbidden}
                if kind=='generation':kwargs['assessment']=forbidden
                if kind=='revision':kwargs['assessment']=forbidden
                result=entry(helper.conn,LEARNER,helper.op,**kwargs)
                self.assertEqual(result['error'],'quality_deadline')
                self.assertEqual(helper.conn.execute('SELECT status FROM study_sessions').fetchone()[0],'failed')

    def test_duplicate_lowlevel_reservation_checks_deadline_before_existing_call(self):
        self.expire()
        grant=reserve_quality_call(self.conn,LEARNER,self.op,'generation')
        self.assertFalse(grant['reserved']);self.assertEqual(grant['operation']['stage'],'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_calls').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0],'failed')

    def test_retry_sweeper_fails_running_retry_record_without_restoring_learning_credit(self):
        helper=retry_fixtures.RetryQuizTests();helper.setUp();self.addCleanup(helper.doCleanups)
        def crash(*args,**kwargs):raise KeyboardInterrupt('simulate process loss')
        with self.assertRaises(KeyboardInterrupt):
            dispatch_retry_quiz(helper.conn,LEARNER,helper.attempt_id,assessment=crash,reviewer=passing_review)
        op,deadline=helper.conn.execute("SELECT operation_id,deadline_at FROM quiz_quality_operations WHERE round='retry'").fetchone()
        sweep_expired_quality_operations(helper.conn,now=deadline)
        self.assertEqual(helper.conn.execute('SELECT status,error FROM retry_quiz_operations').fetchone(),('failed','quality_deadline'))
        self.assertEqual(helper.conn.execute('SELECT status,learning_retry_count FROM study_sessions').fetchone(),('failed',1))
        self.assertEqual(helper.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],1)
        result=dispatch_retry_quiz(helper.conn,LEARNER,helper.attempt_id,assessment=crash,reviewer=passing_review)
        self.assertEqual(result['status'],'failed');self.assertIsNone(result['quiz'])

    def test_published_quiz_and_subsequent_session_state_are_untouched(self):
        self.prepare('approved');publish_reviewed_quiz(self.conn,LEARNER,self.op)
        self.conn.execute("UPDATE study_sessions SET status='grading'");self.conn.commit()
        before=list(self.conn.iterdump())
        self.assertEqual(sweep_expired_quality_operations(self.conn,now=self.deadline()+100)['operations_checked'],0)
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_unpublished_approval_expires_instead_of_staying_pending_save(self):
        self.prepare('approved')
        result=sweep_expired_quality_operations(self.conn,now=self.deadline())
        self.assertEqual((result['operations_expired'],result['sessions_failed']),(1,1))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0],1)
        with self.assertRaises(QualityStateRejected):publish_reviewed_quiz(self.conn,LEARNER,self.op)

    def test_stale_session_and_other_terminal_failure_are_not_overwritten(self):
        self.conn.execute("UPDATE study_sessions SET status='pending_save'");self.conn.commit()
        result=sweep_expired_quality_operations(self.conn,now=self.deadline())
        self.assertEqual(result['operations_expired'],1);self.assertEqual(result['sessions_failed'],0)
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0],'pending_save')
        self.conn.execute("UPDATE quiz_quality_operations SET failure_code='review_uncertainty'");self.conn.commit()
        before=list(self.conn.iterdump())
        self.assertEqual(sweep_expired_quality_operations(self.conn,now=self.deadline()+1)['operations_checked'],0)
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_legacy_deadline_failure_with_reserved_call_and_preparing_session_is_reconciled(self):
        self.conn.execute("UPDATE quiz_quality_operations SET stage='failed',failure_code='quality_deadline'");self.conn.commit()
        result=sweep_expired_quality_operations(self.conn,now=self.deadline())
        self.assertEqual((result['operations_expired'],result['calls_failed'],result['sessions_failed']),(0,1,1))

    def test_wrong_owner_cannot_expire_operation(self):
        with self.assertRaises(QualityStateRejected):expire_quality_operation(self.conn,'learner_other',self.op,now=self.deadline())
        self.assertEqual(self.conn.execute('SELECT stage FROM quiz_quality_operations').fetchone()[0],'generating')

    def test_database_failure_rolls_back_operation_call_and_session_together(self):
        self.conn.execute("CREATE TRIGGER break_expiry BEFORE UPDATE ON study_sessions WHEN NEW.status='failed' BEGIN SELECT RAISE(ABORT,'disk failure'); END");self.conn.commit()
        before=list(self.conn.iterdump())
        with self.assertRaises(sqlite3.Error):sweep_expired_quality_operations(self.conn,now=self.deadline())
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_concurrent_sweepers_expire_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'quality.db'
            with closing(sqlite3.connect(path)) as conn:self.conn.backup(conn)
            deadline=self.deadline()
            def sweep():
                with closing(sqlite3.connect(path,timeout=5)) as conn:
                    return sweep_expired_quality_operations(conn,now=deadline)
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:sweep(),range(2)))
            self.assertEqual(sum(r['operations_expired'] for r in results),1)
            self.assertEqual(sum(r['calls_failed'] for r in results),1)

    def test_batch_limit_and_invalid_arguments(self):
        for now in (True,float('nan'),float('inf')):
            with self.assertRaises(ValueError):sweep_expired_quality_operations(self.conn,now=now)
        for limit in (0,True,1001):
            with self.assertRaises(ValueError):sweep_expired_quality_operations(self.conn,limit=limit)
        self.assertEqual(sweep_expired_quality_operations(self.conn,now=self.deadline(),limit=1)['operations_checked'],1)

    def test_maintenance_command_reconciles_without_a_learner_request(self):
        self.expire()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'quality.db'
            with closing(sqlite3.connect(path)) as conn:self.conn.backup(conn)
            process=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/expire_quality_operations.py'),str(path)],
                                   text=True,capture_output=True,timeout=5)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertEqual(json.loads(process.stdout)['operations_expired'],1)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT status FROM study_sessions').fetchone()[0],'failed')

    def test_late_review_after_sweeper_cannot_overwrite_expiry_or_publish(self):
        self.helper.stage()
        def reviewer(context,**kwargs):
            sweep_expired_quality_operations(self.conn,now=self.deadline())
            return self.helper.report()
        result=dispatch_quality_review(self.conn,LEARNER,self.op,reviewer=reviewer)
        self.assertEqual(result['error'],'quality_deadline')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],0)
