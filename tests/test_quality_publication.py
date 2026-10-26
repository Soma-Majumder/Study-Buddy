"""Exact approved candidate publication, rollback, replay, concurrency and privacy."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from tests import test_quality_storage as fixtures
from study_buddy.controller import publish_reviewed_quiz, dispatch_quality_review, dispatch_quality_revision, submit_answers
from study_buddy.quality_state import QualityStateRejected
from study_buddy.quality_review import candidate_hash
from study_buddy.quality_storage import load_private_candidate

LEARNER = fixtures.LEARNER


class QualityPublicationTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.QualityStorageTests(); self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn, self.op = self.helper.conn, self.helper.op
        self.helper.stage()

    def approve(self):
        return dispatch_quality_review(self.conn, LEARNER, self.op, reviewer=lambda context, **kwargs:self.helper.report())

    def publish(self): return publish_reviewed_quiz(self.conn, LEARNER, self.op)

    def test_only_saved_approved_questions_and_private_keys_are_published(self):
        self.approve(); result = self.publish()
        self.assertTrue(result['published']); self.assertEqual(result['status'], 'awaiting_answers')
        self.assertEqual(len(result['quiz']['questions']), 10)
        for field in ('answer_key', 'correct_option', 'rationale', 'support_quotes'):
            self.assertNotIn(field, json.dumps(result))
        row = self.conn.execute('SELECT candidate_version, candidate_hash, quiz_id FROM quiz_quality_publications').fetchone()
        self.assertEqual(row, (0,candidate_hash(self.helper.candidate),result['quiz']['quiz_id']))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM answer_keys').fetchone()[0],10)
        original = {q['position']:q for q in self.helper.candidate['quiz']['questions']}
        for q in result['quiz']['questions']:
            self.assertEqual((q['prompt'],q['options']), (original[q['position']]['prompt'],original[q['position']]['options']))

    def test_replay_returns_same_ids_without_creating_new_rows_or_calls(self):
        self.approve(); first=self.publish(); before=list(self.conn.iterdump()); second=self.publish()
        self.assertFalse(second['published']); self.assertEqual(second['quiz'],first['quiz'])
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_unreviewed_or_lowlevel_approved_without_report_cannot_publish(self):
        with self.assertRaises(QualityStateRejected): self.publish()
        self.conn.execute("UPDATE quiz_quality_operations SET stage='approved'");self.conn.commit()
        with self.assertRaises(QualityStateRejected): self.publish()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],0)

    def test_wrong_owner_rejected(self):
        self.approve()
        with self.assertRaises(QualityStateRejected): publish_reviewed_quiz(self.conn,'learner_other',self.op)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],0)

    def test_hash_or_key_tampering_blocks_publication(self):
        self.approve()
        self.conn.execute("UPDATE quiz_quality_candidate_keys SET rationale=rationale || 'changed' WHERE position=1"); self.conn.commit()
        with self.assertRaises(QualityStateRejected): self.publish()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],0)

    def test_report_and_completed_call_are_rechecked(self):
        self.approve()
        self.conn.execute("UPDATE quiz_quality_calls SET status='failed' WHERE kind='review'");self.conn.commit()
        with self.assertRaises(QualityStateRejected):self.publish()
        self.conn.execute("UPDATE quiz_quality_calls SET status='completed' WHERE kind='review'")
        self.conn.execute("UPDATE quiz_quality_reports SET report_data='{}'");self.conn.commit()
        with self.assertRaises(QualityStateRejected):self.publish()

    def test_unapproved_source_or_expired_deadline_blocks_new_publication(self):
        self.approve()
        self.conn.execute('UPDATE lessons SET approved=0');self.conn.commit()
        with self.assertRaises(QualityStateRejected):self.publish()
        self.conn.execute('UPDATE lessons SET approved=1')
        self.conn.execute('UPDATE quiz_quality_operations SET started_at=?, deadline_at=?',(time.time()-181,time.time()-1));self.conn.commit()
        with self.assertRaises(QualityStateRejected):self.publish()

    def test_revised_candidate_is_published_instead_of_failed_original(self):
        def bad(context,**kwargs):
            r=self.helper.report();r['quiz_review']['concept_coverage']='fail'
            r['quiz_review']['issues']=[dict(code='missing_coverage',detail='Repeated.',related_positions=[1,2])]
            return r
        dispatch_quality_review(self.conn,LEARNER,self.op,reviewer=bad)
        revised=copy.deepcopy(self.helper.candidate);revised['quiz']['questions'][0]['prompt']+=' Replacement.'
        dispatch_quality_revision(self.conn,LEARNER,self.op,assessment=lambda context,**kwargs:revised,
                                  reviewer=lambda context,**kwargs:self.helper.report(1,revised))
        result=self.publish()
        self.assertEqual(result['quiz']['questions'][0]['prompt'],revised['quiz']['questions'][0]['prompt'])
        self.assertEqual(self.conn.execute('SELECT candidate_version,candidate_hash FROM quiz_quality_publications').fetchone(),(1,candidate_hash(revised)))
        self.assertEqual(load_private_candidate(self.conn,LEARNER,self.op,0),self.helper.candidate)

    def test_save_failure_rolls_back_everything_and_can_retry_without_new_review(self):
        self.approve()
        self.conn.execute("CREATE TRIGGER break_publication BEFORE INSERT ON quiz_quality_publications BEGIN SELECT RAISE(ABORT,'save error'); END");self.conn.commit()
        result=self.publish()
        self.assertEqual((result['status'],result['error']),('teaching','quality_publish_save_failed'))
        for table in ('quizzes','questions','answer_keys','quiz_quality_publications'):
            self.assertEqual(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT stage FROM quiz_quality_operations').fetchone()[0],'approved')
        self.conn.execute('DROP TRIGGER break_publication');self.conn.commit()
        self.assertTrue(self.publish()['published'])
        self.assertEqual(self.conn.execute('SELECT review_count FROM quiz_quality_operations').fetchone()[0],1)
        self.assertIsNone(self.conn.execute('SELECT failure_reason FROM study_sessions').fetchone()[0])

    def test_status_save_failure_rolls_back_quiz_and_binding(self):
        self.approve()
        self.conn.execute("CREATE TRIGGER break_status BEFORE UPDATE ON study_sessions WHEN NEW.status='awaiting_answers' BEGIN SELECT RAISE(ABORT,'status error'); END");self.conn.commit()
        result=self.publish()
        self.assertEqual(result['error'],'quality_publish_save_failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_publications').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],0)

    def test_post_publication_tampering_is_detected_on_replay(self):
        self.approve();self.publish()
        self.conn.execute("UPDATE answer_keys SET rationale=rationale || 'changed'");self.conn.commit()
        with self.assertRaises(QualityStateRejected):self.publish()

    def test_replay_after_submission_does_not_show_old_quiz_or_reset_session(self):
        self.approve();result=self.publish()
        keys={k['position']:k['correct_option'] for k in self.helper.candidate['answer_key']}
        answers={q['question_id']:keys[q['position']] for q in result['quiz']['questions']}
        submit_answers(self.conn,LEARNER,result['quiz']['quiz_id'],answers)
        replay=self.publish()
        self.assertEqual(replay['status'],'grading');self.assertIsNone(replay['quiz']);self.assertIsNone(replay['next_step'])

    def test_concurrent_publication_creates_one_quiz_and_one_binding(self):
        self.approve()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.db'
            with closing(sqlite3.connect(path)) as conn:self.conn.backup(conn)
            def publish():
                with closing(sqlite3.connect(path,timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON')
                    return publish_reviewed_quiz(conn,LEARNER,self.op)
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:publish(),range(2)))
            self.assertEqual(sum(r['published'] for r in results),1)
            self.assertEqual(results[0]['quiz'],results[1]['quiz'])
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM quiz_quality_publications').fetchone()[0],1)

    def test_migration_adds_publication_table_without_changing_existing_data(self):
        sql=(Path(__file__).resolve().parent.parent/'db/migrations/007_quiz_quality_publications.sql').read_text()
        self.conn.execute('DROP TABLE quiz_quality_publications');self.conn.commit()
        before=self.conn.execute('SELECT COUNT(*) FROM quiz_quality_candidates').fetchone()
        self.conn.executescript(sql)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_candidates').fetchone(),before)
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_retry_publication_keeps_focus_and_consumed_learning_retry(self):
        from tests import test_retry_quiz as retry_fixtures
        from study_buddy.quality_state import start_quality_operation, reserve_quality_call
        from study_buddy.quality_storage import store_candidate
        helper=retry_fixtures.RetryQuizTests();helper.setUp();self.addCleanup(helper.doCleanups)
        conn=helper.conn
        sid,data=conn.execute("SELECT session_id,context FROM session_routes WHERE route='reteach'").fetchone()
        handoff=json.loads(data)['tutor_handoff']
        op=start_quality_operation(conn,LEARNER,sid,handoff['handoff_id'],handoff['payload']['explanation_id'],'retry')['operation']['operation_id']
        candidate=helper.assessment(handoff)
        call=reserve_quality_call(conn,LEARNER,op,'generation')
        store_candidate(conn,LEARNER,call['call_id'],candidate)
        def reviewer(context,**kwargs):
            # Synthetic mechanics fixture, not an educational-quality benchmark.
            r=self.helper.report();r.update(context['controller_context'])
            for q in r['questions']:
                q['concept_id']=next(c['concept_id'] for c in candidate['quiz']['questions'] if c['position']==q['position'])
                q['defensible_options']=[next(k['correct_option'] for k in candidate['answer_key'] if k['position']==q['position'])]
                q['support_quotes']=[context['teaching']['explanation_text']]
            return r
        dispatch_quality_review(conn,LEARNER,op,reviewer=reviewer)
        result=publish_reviewed_quiz(conn,LEARNER,op)
        self.assertEqual((result['status'],len(result['quiz']['questions'])),('awaiting_answers',10))
        self.assertEqual(conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0],1)
        concepts={c[0] for c in conn.execute('SELECT concept_id FROM questions WHERE quiz_id=?',(result['quiz']['quiz_id'],))}
        self.assertTrue(concepts.issubset(set(handoff['payload']['focus_concept_ids'])))
        self.assertEqual(conn.execute('SELECT round FROM quizzes WHERE quiz_id=?',(result['quiz']['quiz_id'],)).fetchone()[0],'retry')

    def test_failure_diagnostic_save_failure_propagates_without_false_publication(self):
        self.approve()
        self.conn.execute("CREATE TRIGGER break_binding BEFORE INSERT ON quiz_quality_publications BEGIN SELECT RAISE(ABORT,'binding error'); END")
        self.conn.execute("CREATE TRIGGER break_diagnostic BEFORE UPDATE ON study_sessions WHEN NEW.failure_reason='quality_publish_save_failed' BEGIN SELECT RAISE(ABORT,'diagnostic error'); END")
        self.conn.commit()
        with self.assertRaises(sqlite3.Error):self.publish()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_publications').fetchone()[0],0)

    def test_expired_replay_preserves_existing_publication_without_new_save(self):
        self.approve();first=self.publish()
        self.conn.execute('UPDATE quiz_quality_operations SET started_at=?,deadline_at=?',(time.time()-181,time.time()-1));self.conn.commit()
        replay=self.publish()
        self.assertFalse(replay['published']);self.assertEqual(replay['quiz'],first['quiz'])
