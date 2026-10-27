"""Only a durable request winner may dispatch Tutor and downstream agents."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from tests import test_controller as fixtures
from tests import test_session_flow as flow_fixtures
from tests.quality_fixtures import passing_review
from study_buddy.controller import start_session, start_quiz_session, TutorFailure
from study_buddy.session_flow import run_study_session
from study_buddy.session_start_requests import StartRequestRejected

ROOT=Path(__file__).resolve().parent.parent
LEARNER=fixtures.LEARNER
OPTIONS=dict(request_id='request_start_test',lesson_version=2)


class ReservedSessionStartTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.ControllerTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.conn=self.helper.conn
        self.conn.executescript((ROOT/'db/proposals/session_start_requests.sql').read_text())
        self.helper.assessment_calls=[]

    def start(self,**changes):
        options=dict(OPTIONS);options.update(changes)
        return start_session(self.conn,LEARNER,'lesson_cell_membranes',tutor=self.helper.tutor,**options)

    def test_winner_uses_reserved_ids_and_replay_never_calls_tutor(self):
        first=self.start();second=self.start()
        self.assertTrue(first['reserved']);self.assertFalse(second['reserved'])
        self.assertEqual(first['session_id'],second['session_id']);self.assertIsNone(second['handoff'])
        self.assertIsNone(second['next_step']);self.assertEqual(len(self.helper.calls),1)
        row=self.conn.execute('SELECT session_id,handoff_id,explanation_id FROM session_start_requests').fetchone()
        context=self.helper.calls[0]
        self.assertEqual(row,(context['sessionId'],context['handoffId'],context['explanationId']))
        self.assertEqual(context['lessonVersion'],2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM explanations').fetchone()[0],1)

    def test_quiz_entry_replay_stops_before_any_downstream_call(self):
        def start():return start_quiz_session(self.conn,LEARNER,'lesson_cell_membranes',tutor=self.helper.tutor,
            assessment=self.helper.assessment,reviewer=passing_review,**OPTIONS)
        first=start();self.assertIsNotNone(first['quiz'])
        second=start()
        self.assertEqual(second['session_id'],first['session_id'])
        self.assertEqual(second['status'],'awaiting_answers');self.assertIsNone(second['quiz'])
        self.assertFalse(second['reserved']);self.assertEqual(len(self.helper.assessment_calls),1)
        self.assertEqual(len(self.helper.calls),1)

    def test_failed_tutor_and_changed_inputs_cannot_refresh_claim(self):
        calls=[]
        def failed(context):calls.append(context);raise TutorFailure('model call',1)
        first=start_session(self.conn,LEARNER,'lesson_cell_membranes',tutor=failed,**OPTIONS)
        self.assertEqual(first['status'],'failed')
        second=start_session(self.conn,LEARNER,'lesson_cell_membranes',tutor=failed,**OPTIONS)
        self.assertFalse(second['reserved']);self.assertEqual(second['status'],'failed');self.assertEqual(len(calls),1)
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0],1)
        with self.assertRaises(StartRequestRejected):self.start(lesson_version=3)
        self.assertEqual(len(self.helper.calls),0)

    def test_crashed_tutor_is_not_automatically_resent(self):
        calls=[]
        def crash(context):calls.append(context);raise KeyboardInterrupt('simulate process loss')
        with self.assertRaises(KeyboardInterrupt):
            start_session(self.conn,LEARNER,'lesson_cell_membranes',tutor=crash,**OPTIONS)
        replay=self.start()
        self.assertFalse(replay['reserved']);self.assertEqual(replay['status'],'teaching')
        self.assertEqual(len(calls),1);self.assertEqual(len(self.helper.calls),0)

    def test_reservation_failure_never_calls_tutor(self):
        self.conn.execute("CREATE TRIGGER fail_request BEFORE INSERT ON session_start_requests BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.Error):self.start()
        self.assertEqual(self.helper.calls,[])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0],0)

    def test_duplicate_returns_while_winner_runs_without_holding_a_database_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'live-claim.db'
            with closing(sqlite3.connect(path)) as target:self.conn.backup(target)
            entered=threading.Event();release=threading.Event();calls=[]
            def first():
                with closing(sqlite3.connect(path,timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON')
                    def tutor(context):
                        calls.append(context)
                        self.assertFalse(conn.in_transaction)
                        with closing(sqlite3.connect(path)) as reader:
                            self.assertEqual(reader.execute('SELECT COUNT(*) FROM session_start_requests').fetchone()[0],1)
                        entered.set()
                        if not release.wait(5):raise AssertionError('Test did not release Tutor')
                        return self.helper.tutor(context)
                    return start_session(conn,LEARNER,'lesson_cell_membranes',tutor=tutor,**OPTIONS)
            def duplicate():
                with closing(sqlite3.connect(path,timeout=2)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON')
                    def forbidden(context):raise AssertionError('Duplicate dispatched Tutor')
                    return start_session(conn,LEARNER,'lesson_cell_membranes',tutor=forbidden,**OPTIONS)
            with ThreadPoolExecutor(max_workers=2) as pool:
                future=pool.submit(first)
                try:
                    self.assertTrue(entered.wait(5))
                    replay=pool.submit(duplicate).result(timeout=3)
                    self.assertFalse(replay['reserved']);self.assertEqual(replay['status'],'teaching')
                finally:release.set()
                winner=future.result(timeout=5)
            self.assertEqual(winner['session_id'],replay['session_id']);self.assertEqual(len(calls),1)

    def test_whole_flow_replay_does_not_prompt_for_answers_or_restart_agents(self):
        helper=flow_fixtures.SessionFlowTests();helper.setUp();self.addCleanup(helper.doCleanups)
        helper.conn.executescript((ROOT/'db/proposals/session_start_requests.sql').read_text())
        done=run_study_session(helper.conn,LEARNER,'lesson_cell_membranes',helper.learner(set()),
            tutor=helper.tutor,assessment=helper.assessment,reviewer=passing_review,feedback=helper.feedback,**OPTIONS)
        self.assertEqual(done['status'],'completed')
        def forbidden(*args,**kwargs):raise AssertionError('Replay restarted learning work')
        replay=run_study_session(helper.conn,LEARNER,'lesson_cell_membranes',forbidden,
            tutor=forbidden,assessment=forbidden,reviewer=forbidden,feedback=forbidden,**OPTIONS)
        self.assertEqual(replay['session_id'],done['session_id']);self.assertEqual(replay['status'],'completed')
        self.assertFalse(replay['reserved']);self.assertEqual(replay['next_action'],'read_session_status')
