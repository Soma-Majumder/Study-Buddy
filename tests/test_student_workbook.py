"""Browser/API boundary exercises real controller persistence with fixture agents."""
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import os
from unittest.mock import patch
from scripts.student_workbook import WorkbookServer, LEARNER
from study_buddy.answer_keys import get_answer_key


class StudentWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'workbook.sqlite'
        self.server=WorkbookServer(self.path,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_server)
        self.port=self.server.server_port

    def close_server(self):
        self.server.shutdown();self.thread.join();self.server.server_close()

    def request(self,path,payload=None,token=True,origin=None,method=None):
        headers={}
        if token:headers['X-Workbook-Token']=self.server.token
        if origin:headers['Origin']=origin
        if payload is not None:headers['Content-Type']='application/json'
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        self.addCleanup(conn.close)
        conn.request(method or ('POST' if payload is not None else 'GET'),path,
                     json.dumps(payload) if payload is not None else None,headers)
        response=conn.getresponse();body=response.read();conn.close()
        return response.status,json.loads(body) if 'application/json' in response.getheader('Content-Type','') else body

    def start(self,request_id='workbook_start_test'):
        status,body=self.request('/api/start',dict(request_id=request_id,lesson_id='lesson_cell_membranes',lesson_version=2))
        self.assertEqual(status,200,body);self.assertEqual(body['status'],'awaiting_answers',body)
        return body

    def answer(self,snap,wrong=()):
        with sqlite3.connect(self.path) as conn:keys=get_answer_key(conn,snap['quiz']['quiz_id'])
        return {k['question_id']:('b' if k['correct_option']=='a' else 'a') if i in wrong else k['correct_option']
                for i,k in enumerate(keys,1)}

    def submit(self,snap,wrong=()):
        code,result=self.request('/api/submit',dict(session_id=snap['session_id'],quiz_id=snap['quiz']['quiz_id'],answers=self.answer(snap,wrong)))
        self.assertEqual(code,200,result);self.assertIsNotNone(result['attempts'][-1]['feedback'],result)
        return result

    def test_keyless_start_and_static_private_files_not_served(self):
        snap=self.start();raw=json.dumps(snap)
        self.assertNotIn('correct_option',raw);self.assertNotIn('answer_key',raw);self.assertNotIn('rationale',raw)
        self.assertEqual(len(snap['quiz']['questions']),10)
        for path in ('/student/sample.json','/backend/evals/revisions/2026-10-28-manual-retry-comparison-fix.json','/.student-local/workbook.sqlite'):
            self.assertEqual(self.request(path)[0],404)

    def test_pass_grades_on_server_and_saves_summary(self):
        snap=self.start();graded=self.submit(snap)
        self.assertEqual(graded['attempts'][0]['score'],10)
        self.assertEqual(graded['status'],'grading')
        code,done=self.request('/api/continue',dict(session_id=snap['session_id']))
        self.assertEqual(code,200,done);self.assertEqual(done['status'],'completed')
        self.assertEqual(done['summary']['learning_retry_count'],0)
        self.assertEqual(self.request('/api/session?session_id='+snap['session_id'])[1]['summary'],done['summary'])

    def test_arbitrary_missed_concepts_receive_one_focused_retry(self):
        snap=self.start();graded=self.submit(snap,wrong=(1,2,8,10))
        self.assertEqual(graded['attempts'][0]['score'],6)
        code,retry=self.request('/api/continue',dict(session_id=snap['session_id']))
        self.assertEqual(code,200,retry);self.assertEqual(retry['status'],'awaiting_answers',retry)
        self.assertEqual(retry['quiz']['round'],'retry');self.assertEqual(len(retry['quiz']['questions']),10)
        expected={item['concept_id'] for item in graded['attempts'][0]['items'] if not item['is_correct']}
        self.assertEqual({c['concept_id'] for c in retry['explanation']['concepts']},expected)
        # The new unanswered retry has no key even though the first attempt has feedback.
        self.assertNotIn('correct_option',json.dumps(retry['quiz']))
        graded2=self.submit(retry,wrong=tuple(range(1,11)))
        code,done=self.request('/api/continue',dict(session_id=snap['session_id']))
        self.assertEqual(code,200,done);self.assertEqual(done['status'],'completed',done)
        self.assertEqual(done['summary']['learning_retry_count'],1)
        self.assertEqual(len(done['summary']['attempts']),2)
        calls=dict(self.server.agents.calls)
        self.assertEqual(self.request('/api/continue',dict(session_id=snap['session_id']))[1]['status'],'completed')
        self.assertEqual(calls,self.server.agents.calls)

    def test_duplicate_starts_and_answers_do_not_redispatch(self):
        snap=self.start();calls=dict(self.server.agents.calls);again=self.start()
        self.assertEqual(again['session_id'],snap['session_id']);self.assertEqual(calls,self.server.agents.calls)
        graded=self.submit(snap);calls=dict(self.server.agents.calls);again=self.submit(snap)
        self.assertEqual(again['attempts'],graded['attempts']);self.assertEqual(calls,self.server.agents.calls)
        changed=self.answer(snap,wrong=(1,))
        self.assertEqual(self.request('/api/submit',dict(session_id=snap['session_id'],quiz_id=snap['quiz']['quiz_id'],answers=changed))[0],409)

    def test_incomplete_answers_do_not_create_attempt(self):
        snap=self.start()
        code,_=self.request('/api/submit',dict(session_id=snap['session_id'],quiz_id=snap['quiz']['quiz_id'],answers={}))
        self.assertEqual(code,409)
        with sqlite3.connect(self.path) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],0)

    def test_cross_origin_missing_token_and_identity_override_rejected(self):
        payload=dict(request_id='security_test_start',lesson_id='lesson_cell_membranes',lesson_version=2)
        self.assertEqual(self.request('/api/start',payload,token=False)[0],403)
        self.assertEqual(self.request('/api/start',payload,origin='https://evil.example')[0],403)
        self.assertEqual(self.request('/api/session?session_id=unknown',token=False)[0],403)
        self.assertEqual(self.request('/api/start',dict(payload,learner_id='learner_a1b2c3d4e5f6'))[0],409)
        with sqlite3.connect(self.path) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0],0)

    def test_owner_is_checked_on_reads_and_mutations(self):
        snap=self.start()
        with sqlite3.connect(self.path) as conn:
            conn.execute('INSERT INTO learners VALUES (?)',('learner_a1b2c3d4e5f6',))
            conn.execute('UPDATE study_sessions SET learner_id=?',('learner_a1b2c3d4e5f6',))
        self.assertEqual(self.request('/api/session?session_id='+snap['session_id'])[0],404)
        self.assertEqual(self.request('/api/continue',dict(session_id=snap['session_id']))[0],409)
        self.assertEqual(self.request('/api/submit',dict(session_id=snap['session_id'],quiz_id=snap['quiz']['quiz_id'],answers={}))[0],409)

    def test_progress_survives_server_restart_and_api_has_no_live_mode(self):
        snap=self.start()
        # Reopen a second server on the same database; the first server stays idle.
        other=WorkbookServer(self.path,0)
        try:
            from scripts.student_workbook import connect,snapshot
            with connect(self.path) as conn:self.assertEqual(snapshot(conn,snap['session_id'])['quiz'],snap['quiz'])
            self.assertNotEqual(other.token,self.server.token)
        finally:other.server_close()
        code,config=self.request('/api/config',token=False)
        self.assertEqual(config['mode'],'real_controller_offline_fixtures');self.assertFalse(config['paid_calls_enabled'])
        self.assertEqual(self.request('/api/live',{})[0],404)

class LiveWorkbookWiringTests(unittest.TestCase):
    setUp = StudentWorkbookTests.setUp
    close_server = StudentWorkbookTests.close_server
    request = StudentWorkbookTests.request
    start = StudentWorkbookTests.start
    answer = StudentWorkbookTests.answer
    submit = StudentWorkbookTests.submit
    """Live-mode routing is exercised with fake transports, never Claude."""
    # Do not inherit the base scenarios: they expect the offline config/profile.
    def switch_live(self, *, allow=False, key='offline-test-key', consent='false'):
        self.close_server()
        env=patch.dict(os.environ, {'ANTHROPIC_API_KEY':key,'STUDY_BUDDY_ALLOW_PAID_CLAUDE':consent})
        env.start();self.addCleanup(env.stop)
        self.path=Path(self.directory.name)/'live.sqlite'
        self.server=WorkbookServer(self.path,0,agent_mode='live',allow_paid_calls=allow)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.port=self.server.server_port

    def test_live_adapters_are_exact_existing_functions(self):
        from scripts.student_workbook import LiveAgents
        from study_buddy.controller import run_tutor
        from study_buddy.assessment import run_assessment, run_assessment_revision
        from study_buddy.reviewer import run_reviewer
        from study_buddy.feedback_runner import run_feedback
        live=LiveAgents()
        for actual,expected in ((live.tutor,run_tutor),(live.assessment,run_assessment),
                (live.reviewer,run_reviewer),(live.revision_assessment,run_assessment_revision),(live.feedback,run_feedback)):
            self.assertIs(actual,expected)

    def test_live_mode_without_explicit_optin_stops_before_session_or_provider(self):
        self.switch_live(allow=False,consent='true')
        code,config=self.request('/api/config',token=False)
        self.assertEqual(config['mode'],'real_controller_live_agents');self.assertFalse(config['paid_calls_enabled'])
        with patch('scripts.student_workbook.start_quiz_session') as start:
            code,body=self.request('/api/start',dict(request_id='live_blocked_test',lesson_id='lesson_cell_membranes',lesson_version=2))
        self.assertEqual(code,409);start.assert_not_called()
        with sqlite3.connect(self.path) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0],0)

    def test_live_reads_never_dispatch_and_config_never_contains_key(self):
        self.switch_live(allow=True,consent='true')
        with patch('scripts.student_workbook.start_quiz_session') as start, patch('scripts.student_workbook.run_assessment') as assess:
            code,config=self.request('/api/config',token=False)
            self.assertTrue(config['paid_calls_enabled'])
            self.assertNotIn('offline-test-key',json.dumps(config))
            self.assertEqual(self.request('/api/lessons')[0],200)
            self.assertEqual(self.request('/student/')[0],200)
            self.assertEqual(self.request('/api/session?session_id=missing')[0],404)
            start.assert_not_called();assess.assert_not_called()

    def test_live_start_injects_real_adapters_and_bound_revision(self):
        self.switch_live(allow=True,consent='true')
        from scripts.student_workbook import LIVE_LEARNER, LIVE_MODE
        def captured(conn, learner, lesson, **kwargs):
            self.assertEqual(learner,LIVE_LEARNER)
            self.assertIs(kwargs['tutor'],self.server.agents.tutor)
            self.assertIs(kwargs['assessment'],self.server.agents.assessment)
            self.assertIs(kwargs['reviewer'],self.server.agents.reviewer)
            self.assertIs(kwargs['revision_assessment'],self.server.agents.revision_assessment)
            self.assertEqual(kwargs['request_id'],'live_wiring_test')
            return dict(session_id='session_placeholder')
        with patch('scripts.student_workbook.start_quiz_session',side_effect=captured) as start, patch(
                'scripts.student_workbook.snapshot',return_value=dict(mode=LIVE_MODE,status='awaiting_answers')):
            code,body=self.request('/api/start',dict(request_id='live_wiring_test',lesson_id='lesson_cell_membranes',lesson_version=2))
        self.assertEqual(code,200);start.assert_called_once()

    def test_actual_saved_tutor_text_is_returned_not_replaced_by_source(self):
        marker='This is the actual saved Tutor explanation, distinct from a source excerpt.'
        tutor=self.server.agents.tutor
        def generated(context):
            output=tutor(context)
            output['handoff']['payload']['explanation_text']+='\n\n'+marker
            return output
        with patch.object(self.server.agents,'tutor',side_effect=generated):
            snap=self.start()
        code,loaded=self.request('/api/session?session_id='+snap['session_id'])
        self.assertEqual(code,200);self.assertTrue(loaded['explanation']['text'].endswith(marker))
        self.assertTrue(all('text' not in c for c in loaded['explanation']['concepts']))

    def test_offline_database_cannot_be_reopened_as_live(self):
        from scripts.student_workbook import prepare_database,LIVE_LEARNER
        with self.assertRaises(ValueError):prepare_database(self.path,LIVE_LEARNER)

    def test_live_mode_full_controller_path_with_stubbed_provider_boundaries(self):
        self.switch_live(allow=True,consent='true')
        from scripts.student_workbook import WorkbookAgents
        stubs=WorkbookAgents(lambda line:None)
        # Deliberately substitute transport boundaries only for this offline test.
        for name in ('tutor','assessment','reviewer','feedback','revision_assessment'):
            setattr(self.server.agents,name,getattr(stubs,name))
        snap=self.start();self.assertEqual(snap['mode'],'real_controller_live_agents')
        self.submit(snap,wrong=(1,2,3,4))
        code,retry=self.request('/api/continue',dict(session_id=snap['session_id']))
        self.assertEqual(code,200,retry);self.assertEqual(retry['quiz']['round'],'retry')
        self.submit(retry)
        code,done=self.request('/api/continue',dict(session_id=snap['session_id']))
        self.assertEqual(code,200,done);self.assertEqual(done['status'],'completed')
        self.assertEqual(done['summary']['learning_retry_count'],1)
        self.assertEqual(stubs.calls,dict(tutor=2,assessment=2,reviewer=2,feedback=2))

    def test_trusted_env_cannot_grant_paid_consent_or_execute_shell(self):
        from scripts.student_workbook import load_trusted_env
        config=Path(self.directory.name)/'trusted.env'
        marker=Path(self.directory.name)/'must-not-exist'
        config.write_text('ANTHROPIC_API_KEY="literal-test-key"\nSTUDY_BUDDY_ALLOW_PAID_CLAUDE=true\nNODE_OPTIONS=bad\nIGNORED=$(touch '+str(marker)+')\n')
        with patch.dict(os.environ,{},clear=True):
            load_trusted_env(config)
            self.assertEqual(os.environ['ANTHROPIC_API_KEY'],'literal-test-key')
            self.assertNotIn('STUDY_BUDDY_ALLOW_PAID_CLAUDE',os.environ)
            self.assertNotIn('NODE_OPTIONS',os.environ)
        self.assertFalse(marker.exists())

if __name__=='__main__':unittest.main()
