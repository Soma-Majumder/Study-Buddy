import subprocess
import sys
from threading import Event, Thread
import unittest
from unittest.mock import patch
from study_buddy.cancellation import WorkRegistry, AgentWorkCancelled, run_agent_process, track_agent_process


class CancellationTests(unittest.TestCase):
    def test_default_adapter_behavior_remains_compatible(self):
        with patch('study_buddy.cancellation.subprocess.run',return_value='unchanged') as run:
            self.assertEqual(run_agent_process(['node'],input='{}',text=True,capture_output=True,cwd='.',timeout=10),'unchanged')
            run.assert_called_once()

    def test_cancel_kills_a_running_local_process_and_blocks_next_dispatch(self):
        registry=WorkRegistry();attached=Event();cancelled=Event();processes=[]
        def track(process):
            track_agent_process(process);processes.append(process);attached.set()
        def worker():
            try:
                with registry.work(('request_id','request_process')):
                    run_agent_process([sys.executable,'-c','import time; time.sleep(30)'],text=True,capture_output=True,timeout=35)
            except AgentWorkCancelled:cancelled.set()
        with patch('study_buddy.cancellation.track_agent_process',side_effect=track):
            thread=Thread(target=worker);thread.start()
            try:
                self.assertTrue(attached.wait(3))
                registry.cancel([('request_id','request_process')]);thread.join(3)
                self.assertFalse(thread.is_alive());self.assertTrue(cancelled.is_set())
                self.assertIsNotNone(processes[0].poll())
                with self.assertRaises(AgentWorkCancelled):
                    with registry.work(('request_id','request_process')):self.fail('Cancelled work restarted')
            finally:
                registry.cancel([('request_id','request_process')]);thread.join(3)

    def test_cancellation_is_scoped_and_idempotent(self):
        registry=WorkRegistry();registry.cancel([('session_id','old')]);registry.cancel([('session_id','old')])
        with registry.work(('session_id','new')):
            result=run_agent_process([sys.executable,'-c','print("ready")'],text=True,capture_output=True,timeout=3)
        self.assertEqual(result.stdout.strip(),'ready')
        self.assertEqual(registry.active,{})
