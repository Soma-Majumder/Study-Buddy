"""Worker closes abandoned feedback/reteaching without controller revisits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from tests import test_session_flow as fixtures
from tests.quality_fixtures import passing_review
from study_buddy.answer_keys import get_answer_key
from study_buddy.controller import (start_quiz_session, start_session, submit_answers, explain_results,
    decide_next_step, dispatch_reteaching, get_session_status)
from study_buddy.operation_deadlines import sweep_expired_agent_operations
from study_buddy.maintenance_worker import run_maintenance_cycle
from study_buddy.quality_state import start_quality_operation
from study_buddy.session_start_requests import reserve_session_start

LEARNER = fixtures.LEARNER
LESSON = 'lesson_cell_membranes'


def crash(*args, **kwargs):
    raise KeyboardInterrupt('Simulated process loss')


def forbidden(*args, **kwargs):
    raise AssertionError('Expiry dispatched an agent')


class AgentOperationExpiryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.h = fixtures.SessionFlowTests(); self.h.setUp(); self.addCleanup(self.h.doCleanups)
        self.conn = self.h.conn
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'expiry.db'

    def attempt(self):
        started = start_quiz_session(self.conn, LEARNER, LESSON, tutor=self.h.tutor,
            assessment=self.h.assessment, reviewer=passing_review)
        keys = get_answer_key(self.conn, started['quiz']['quiz_id'])
        answers = {k['question_id']: ('b' if k['correct_option']=='a' else 'a') if i in (3,4,6,7) else k['correct_option']
                   for i,k in enumerate(keys,1)}
        return submit_answers(self.conn, LEARNER, started['quiz']['quiz_id'], answers)

    def prepare(self, kind):
        result = self.attempt()
        aid = result['attempt_id']
        if kind == 'feedback':
            with self.assertRaises(KeyboardInterrupt):
                explain_results(self.conn, LEARNER, aid, assessment=crash)
        else:
            explain_results(self.conn, LEARNER, aid, assessment=self.h.feedback)
            decide_next_step(self.conn, LEARNER, aid)
            if kind == 'dispatched':
                with self.assertRaises(KeyboardInterrupt):
                    dispatch_reteaching(self.conn, LEARNER, aid, tutor=crash)
        return result

    def age(self, kind):
        duration = 180 if kind == 'feedback' else 120
        self.conn.execute(f'UPDATE {kind}_operation_deadlines SET started_at=0,deadline_at=?', (duration,))
        self.conn.commit()

    def save(self):
        with closing(sqlite3.connect(self.path)) as disk: self.conn.backup(disk)

    def test_worker_expires_abandoned_work_preserving_results_and_claims(self):
        for kind in ('feedback','reserved','dispatched'):
            with self.subTest(kind=kind):
                result = self.prepare(kind)
                actual = 'feedback' if kind=='feedback' else 'reteaching'
                self.age(actual); self.save()
                with closing(sqlite3.connect(self.path)) as conn:
                    before = {t:conn.execute(f'SELECT * FROM {t}').fetchall() for t in
                              ('results','responses','weak_concepts','handoff_repairs','feedback_operation_deadlines','reteaching_operation_deadlines')}
                counts = run_maintenance_cycle(self.path,limit=1)
                self.assertEqual(counts[actual+'_operations_expired'],1)
                self.assertEqual(counts['sessions_failed'],1)
                with closing(sqlite3.connect(self.path)) as conn:
                    status = get_session_status(conn, LEARNER, result['session_id'])
                    self.assertEqual((status['status'],status['next_action']),('failed','start_new_session'))
                    self.assertEqual(before,{t:conn.execute(f'SELECT * FROM {t}').fetchall() for t in before})
                    if actual=='feedback':
                        replay = explain_results(conn, LEARNER, result['attempt_id'], assessment=forbidden)
                    else:
                        replay = dispatch_reteaching(conn, LEARNER, result['attempt_id'], tutor=forbidden)
                    self.assertEqual(replay['operation_status'],'failed')
                self.assertTrue(all(v==0 for v in run_maintenance_cycle(self.path).values()))
                # Reconcile the in-memory copy too before creating the next scenario.
                sweep_expired_agent_operations(self.conn,actual)

    def test_deadline_boundary_batch_limit_and_idempotence(self):
        for _ in range(3): self.prepare('feedback')
        self.age('feedback')
        self.assertEqual(sweep_expired_agent_operations(self.conn,'feedback',now=179.999)['feedback_operations_checked'],0)
        for _ in range(3):
            self.assertEqual(sweep_expired_agent_operations(self.conn,'feedback',now=180,limit=1)['feedback_operations_expired'],1)
        self.assertEqual(sweep_expired_agent_operations(self.conn,'feedback',now=180)['feedback_operations_expired'],0)

    def test_ready_feedback_and_ready_tutor_handoff_are_preserved(self):
        result = self.prepare('reserved')
        dispatch_reteaching(self.conn, LEARNER, result['attempt_id'], tutor=self.h.tutor)
        self.age('feedback'); self.age('reteaching')
        before=list(self.conn.iterdump())
        self.assertEqual(sweep_expired_agent_operations(self.conn,'feedback')['feedback_operations_checked'],0)
        self.assertEqual(sweep_expired_agent_operations(self.conn,'reteaching')['reteaching_operations_checked'],0)
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_all_four_sweeps_contribute_to_worker_totals(self):
        self.prepare('feedback'); self.prepare('dispatched')
        self.age('feedback'); self.age('reteaching')
        reserve_session_start(self.conn,LEARNER,'mixed_expiry_start',LESSON,2)
        self.conn.execute("UPDATE session_start_requests SET created_at='2000-01-01T00:00:00.000Z'")
        self.conn.commit()
        started=start_session(self.conn,LEARNER,LESSON,tutor=self.h.tutor)
        h=started['handoff']
        start_quality_operation(self.conn,LEARNER,started['session_id'],h['handoff_id'],h['payload']['explanation_id'],'first')
        self.conn.execute('UPDATE quiz_quality_operations SET started_at=0,deadline_at=180 WHERE session_id=?',(started['session_id'],))
        self.conn.commit();self.save()
        counts=run_maintenance_cycle(self.path,limit=1)
        self.assertEqual(counts['sessions_failed'],4)
        for field in ('operations_expired','session_starts_expired','feedback_operations_expired','reteaching_operations_expired'):
            self.assertEqual(counts[field],1)

    def test_missing_timing_is_an_explicit_failure_and_never_recreated(self):
        self.prepare('feedback')
        self.conn.execute('DELETE FROM feedback_operation_deadlines');self.conn.commit()
        before=list(self.conn.iterdump())
        with self.assertRaises(ValueError): sweep_expired_agent_operations(self.conn,'feedback')
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_failure_rolls_back_whole_agent_expiry_batch(self):
        for _ in range(2): self.prepare('feedback')
        self.age('feedback')
        self.conn.execute("CREATE TRIGGER fail_second BEFORE UPDATE ON study_sessions WHEN NEW.status='failed' AND (SELECT COUNT(*) FROM study_sessions WHERE status='failed')=1 BEGIN SELECT RAISE(ABORT,'fixture'); END")
        self.conn.commit();before=list(self.conn.iterdump())
        with self.assertRaises(sqlite3.IntegrityError): sweep_expired_agent_operations(self.conn,'feedback')
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_invalid_configuration_and_pending_work_are_rejected(self):
        for kind, kwargs in [('unknown',{}),('feedback',{'limit':True}),('reteaching',{'limit':0}),('feedback',{'now':float('nan')})]:
            with self.assertRaises(ValueError): sweep_expired_agent_operations(self.conn,kind,**kwargs)
        self.conn.execute('UPDATE learners SET learner_id=learner_id')
        with self.assertRaises(ValueError): sweep_expired_agent_operations(self.conn,'feedback')
        self.assertTrue(self.conn.in_transaction);self.conn.rollback()

    def test_worker_expiry_wins_against_late_feedback_and_tutor(self):
        for kind in ('feedback','reteaching'):
            with self.subTest(kind=kind):
                result=self.attempt();aid=result['attempt_id']
                if kind=='reteaching':
                    explain_results(self.conn,LEARNER,aid,assessment=self.h.feedback)
                    decide_next_step(self.conn,LEARNER,aid)
                self.save(); entered,release=threading.Event(),threading.Event()
                def winner():
                    with closing(sqlite3.connect(self.path)) as conn:
                        conn.execute('PRAGMA foreign_keys=ON')
                        def callback(context,**kwargs):
                            self.assertFalse(conn.in_transaction);entered.set()
                            if not release.wait(10):raise AssertionError('Callback not released')
                            return self.h.feedback(context,**kwargs) if kind=='feedback' else self.h.tutor(context)
                        return explain_results(conn,LEARNER,aid,assessment=callback) if kind=='feedback' else dispatch_reteaching(conn,LEARNER,aid,tutor=callback)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(winner)
                    try:
                        self.assertTrue(entered.wait(5))
                        with closing(sqlite3.connect(self.path)) as conn:
                            duration=180 if kind=='feedback' else 120
                            conn.execute(f'UPDATE {kind}_operation_deadlines SET started_at=0,deadline_at=?',(duration,));conn.commit()
                            original_feedback=conn.execute('SELECT feedback FROM results WHERE attempt_id=?',(aid,)).fetchone()[0]
                        self.assertEqual(run_maintenance_cycle(self.path)[kind+'_operations_expired'],1)
                        self.assertFalse(future.done())
                    finally:release.set()
                    out=future.result(timeout=5)
                    self.assertEqual(out['operation_status'],'failed')
                with closing(sqlite3.connect(self.path)) as conn:
                    self.assertEqual(conn.execute('SELECT feedback FROM results WHERE attempt_id=?',(aid,)).fetchone()[0],original_feedback)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM explanations WHERE round='retry'").fetchone()[0],0)
                    self.assertEqual(conn.execute('SELECT failure_reason FROM study_sessions WHERE session_id=?',(result['session_id'],)).fetchone()[0],kind+'_deadline')
