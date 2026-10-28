"""Real child-process handshake and file SQLite; no model calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
import shutil
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests import test_controller as fixtures
from study_buddy.controller import start_session, TutorFailure
from study_buddy.session_start_requests import reserve_session_start
from study_buddy.tutor_bridge import run_durable_tutor, reserve_tutor_repair


class DurableTutorRepairTests(unittest.TestCase):
    def setUp(self):
        helper=fixtures.ControllerTests();helper.setUp();self.addCleanup(helper.doCleanups)
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'session.db'
        self.conn=sqlite3.connect(self.path);self.addCleanup(self.conn.close)
        helper.conn.backup(self.conn);self.conn.execute('PRAGMA foreign_keys=ON')
        grant=reserve_session_start(self.conn,fixtures.LEARNER,'durable_repair_test','lesson_cell_membranes',2)
        self.context=dict(lessonId=grant['lesson_id'],sessionId=grant['session_id'],handoffId=grant['handoff_id'],
                          explanationId=grant['explanation_id'],round='first')
        self.output=helper.tutor(self.context)

    def command(self,body):
        prefix="import sys,json,sqlite3\nrequest=json.loads(sys.stdin.readline())\n"
        return [sys.executable,'-u','-c',prefix+body]

    def result_line(self,count):
        return 'print('+repr(json.dumps(dict(ok=True,result=dict(self.output,repairCount=count))))+',flush=True)\n'

    def count(self):
        return self.conn.execute('SELECT repair_count FROM handoff_repairs WHERE handoff_id=?',(self.context['handoffId'],)).fetchone()[0]

    def ask(self):
        return "print(json.dumps({'event':'reserve_tutor_repair'}),flush=True)\nack=json.loads(sys.stdin.readline())\nassert ack['repair_reserved'] is True\n"

    def test_valid_first_response_leaves_shared_repair_available(self):
        result=run_durable_tutor(self.conn,self.context,command=self.command(self.result_line(0)))
        self.assertEqual(result['repairCount'],0);self.assertEqual(self.count(),0)

    def test_child_observes_committed_reservation_before_second_call(self):
        check='db=sqlite3.connect('+repr(str(self.path))+')\nassert db.execute("SELECT repair_count FROM handoff_repairs WHERE handoff_id=?",('+repr(self.context['handoffId'])+',)).fetchone()==(1,)\ndb.close()\n'
        result=run_durable_tutor(self.conn,self.context,command=self.command(self.ask()+check+self.result_line(1)))
        self.assertEqual(result['repairCount'],1);self.assertEqual(self.count(),1)
        self.assertFalse(self.conn.in_transaction)

    def test_process_loss_preserves_budget_after_database_reopen_and_blocks_redispatch(self):
        with self.assertRaises(TutorFailure) as error:
            run_durable_tutor(self.conn,self.context,command=self.command(self.ask()+'sys.exit(2)\n'))
        self.assertEqual(error.exception.repair_count,1)
        with closing(sqlite3.connect(self.path)) as reopened:
            with patch('study_buddy.tutor_bridge.subprocess.Popen') as spawn:
                with self.assertRaises(TutorFailure):run_durable_tutor(reopened,self.context)
                spawn.assert_not_called()

    def test_database_failure_never_authorizes_second_call(self):
        self.conn.execute("CREATE TRIGGER no_repair BEFORE UPDATE OF repair_count ON handoff_repairs BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(TutorFailure):
            run_durable_tutor(self.conn,self.context,command=self.command(self.ask()+self.result_line(1)))
        self.assertEqual(self.count(),0)

    def test_interrupt_after_commit_never_refunds_budget(self):
        def crash(conn,context):
            self.assertTrue(reserve_tutor_repair(conn,context));raise KeyboardInterrupt()
        with patch('study_buddy.tutor_bridge.reserve_tutor_repair',side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                run_durable_tutor(self.conn,self.context,command=self.command(self.ask()+self.result_line(1)))
        self.assertEqual(self.count(),1)

    def test_duplicate_repair_request_fails_with_only_one_spent_credit(self):
        extra="print(json.dumps({'event':'reserve_tutor_repair'}),flush=True)\n"
        with self.assertRaises(TutorFailure) as error:
            run_durable_tutor(self.conn,self.context,command=self.command(self.ask()+extra+'sys.stdin.readline()\n'))
        self.assertEqual(error.exception.repair_count,1);self.assertEqual(self.count(),1)

    def test_changed_count_without_reservation_is_rejected(self):
        with self.assertRaises(TutorFailure):
            run_durable_tutor(self.conn,self.context,command=self.command(self.result_line(1)))
        self.assertEqual(self.count(),0)

    def test_two_connections_can_reserve_only_one_repair(self):
        def reserve():
            with closing(sqlite3.connect(self.path,timeout=3)) as conn:
                return reserve_tutor_repair(conn,self.context)
        with ThreadPoolExecutor(max_workers=2) as pool:
            answers=list(pool.map(lambda _:reserve(),range(2)))
        self.assertEqual(sorted(answers),[False,True])

    def test_expired_start_denies_repair_and_persists_expiry(self):
        # Freeze the clock beyond the saved start deadline; do not rewrite immutable timestamps.
        with patch('study_buddy.session_start_requests.time.time',return_value=9_999_999_999):
            self.assertFalse(reserve_tutor_repair(self.conn,self.context))
        self.assertEqual(self.count(),0)
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0],'failed')

    def test_default_first_round_controller_uses_durable_bridge(self):
        helper=fixtures.ControllerTests();helper.setUp();self.addCleanup(helper.doCleanups)
        def bridge(conn,context):
            self.assertIs(conn,helper.conn);return helper.tutor(context)
        with patch('study_buddy.tutor_bridge.run_durable_tutor',side_effect=bridge) as run:
            result=start_session(helper.conn,fixtures.LEARNER,'lesson_cell_membranes',
                                 request_id='default_bridge_test',lesson_version=2)
        self.assertEqual(result['next_step'],'assessment');run.assert_called_once()

    def test_default_reteaching_uses_durable_bridge(self):
        from tests.test_reteaching import ReteachingTests
        from study_buddy.reteaching import dispatch_reteaching
        helper=ReteachingTests();helper.setUp();self.addCleanup(helper.doCleanups)
        with patch('study_buddy.tutor_bridge.run_durable_tutor',side_effect=lambda conn,context:helper.tutor(context)) as run:
            result=dispatch_reteaching(helper.conn,fixtures.LEARNER,helper.attempt_id)
        self.assertEqual(result['next_step'],'assessment');run.assert_called_once()

    def test_stalled_child_is_killed_at_bridge_deadline(self):
        with self.assertRaises(TutorFailure):
            run_durable_tutor(self.conn,self.context,command=self.command('import time;time.sleep(10)\n'),timeout_seconds=0.1)
        self.assertEqual(self.count(),0)

    def test_real_node_command_repairs_only_after_python_handshake(self):
        node=os.environ.get('STUDY_BUDDY_NODE') or shutil.which('node')
        if not node:self.skipTest('Node runtime unavailable')
        root=Path(__file__).resolve().parent.parent
        calls=Path(self.directory.name)/'fake-calls.txt'
        preload=Path(self.directory.name)/'fake-provider.mjs'
        preload.write_text("""import {readFileSync,writeFileSync} from 'node:fs';
const fixture=JSON.parse(readFileSync(FIXTURE,'utf8'));
let calls=0;
globalThis.fetch=async (_url,options)=>{
  calls++;writeFileSync(CALLS,String(calls));
  const input=JSON.parse(JSON.parse(options.body).messages[0].content);
  const h=structuredClone(fixture);
  h.handoff_id=input.controller_context.handoff_id;
  h.context.learner_session_id=input.controller_context.learner_session_id;
  h.payload.explanation_id=input.controller_context.explanation_id;
  h.sources=input.sources;
  if(calls===1)h.payload.concepts[0].concept_id='c_invented';
  return {ok:true,json:async()=>({stop_reason:'end_turn',model:'offline-fixture',
    content:[{type:'text',text:JSON.stringify(h)}],usage:{input_tokens:1,output_tokens:1}})};
};
""".replace('FIXTURE',json.dumps(str(root/'schemas/v1/examples/tutor_to_assessment.example.json')))
     .replace('CALLS',json.dumps(str(calls))))
        # The transport is replaced in this child; no network or paid calls occur.
        env=dict(STUDY_BUDDY_NODE=node,NODE_OPTIONS='--import='+json.dumps(str(preload)),
                 ANTHROPIC_API_KEY='offline-fake-key',STUDY_BUDDY_ALLOW_PAID_CLAUDE='true')
        with patch.dict(os.environ,env):
            result=run_durable_tutor(self.conn,self.context)
        self.assertEqual(result['repairCount'],1)
        self.assertEqual(calls.read_text(),'2')
        self.assertEqual(self.count(),1)
        self.assertEqual(result['handoff']['handoff_id'],self.context['handoffId'])

    def test_real_tutor_requires_request_identity_before_any_dispatch_or_write(self):
        from study_buddy.session_start_requests import StartRequestRejected
        before=self.conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0]
        for options in ({},{'request_id':'required_start_test'},{'lesson_version':2}):
            with patch('study_buddy.tutor_bridge.run_durable_tutor') as run:
                with self.assertRaises(StartRequestRejected):
                    start_session(self.conn,fixtures.LEARNER,'lesson_cell_membranes',**options)
                run.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0],before)

    def test_duplicate_real_entry_calls_durable_bridge_once(self):
        output=self.output
        def bridge(conn,context):
            handoff=json.loads(json.dumps(output))
            handoff['handoff']['handoff_id']=context['handoffId']
            handoff['handoff']['context']['learner_session_id']=context['sessionId']
            handoff['handoff']['payload']['explanation_id']=context['explanationId']
            return handoff
        options=dict(request_id='real_duplicate_test',lesson_version=2)
        with patch('study_buddy.tutor_bridge.run_durable_tutor',side_effect=bridge) as run:
            first=start_session(self.conn,fixtures.LEARNER,'lesson_cell_membranes',**options)
            second=start_session(self.conn,fixtures.LEARNER,'lesson_cell_membranes',**options)
        self.assertEqual(first['session_id'],second['session_id'])
        self.assertFalse(second['reserved']);run.assert_called_once()

    def test_reteaching_repair_persists_without_granting_another_learning_retry(self):
        from tests.test_reteaching import ReteachingTests
        from study_buddy.reteaching import dispatch_reteaching
        helper=ReteachingTests();helper.setUp();self.addCleanup(helper.doCleanups)
        def tutor(context):
            result=helper.tutor(context);result['repairCount']=1
            code=self.ask()+'print('+repr(json.dumps(dict(ok=True,result=result)))+',flush=True)\n'
            return run_durable_tutor(helper.conn,context,command=self.command(code))
        result=dispatch_reteaching(helper.conn,fixtures.LEARNER,helper.attempt_id,tutor=tutor)
        self.assertEqual(result['next_step'],'assessment')
        self.assertEqual(helper.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0],1)
        self.assertEqual(helper.conn.execute('SELECT repair_count FROM handoff_repairs WHERE handoff_id=?',
            (result['handoff']['handoff_id'],)).fetchone()[0],1)

    def test_inactive_session_cannot_authorize_repair(self):
        self.conn.execute("UPDATE study_sessions SET status='failed'");self.conn.commit()
        self.assertFalse(reserve_tutor_repair(self.conn,self.context));self.assertEqual(self.count(),0)
