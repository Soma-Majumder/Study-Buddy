"""Reservation-only prototype; SQLite atomicity/concurrency without agent calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from study_buddy.lessons import load_lessons
from study_buddy.session_start_requests import reserve_session_start, StartRequestRejected

ROOT=Path(__file__).resolve().parent.parent
LEARNER='learner_a1b2c3d4e5f6'
REQUEST='request_test_0001'


class SessionStartRequestTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.addCleanup(self.conn.close)
        self.conn.executescript((ROOT/'db/schema.sql').read_text())
        load_lessons(self.conn)
        self.conn.execute('INSERT INTO learners VALUES (?)',(LEARNER,));self.conn.commit()

    def reserve(self,**overrides):
        args=dict(learner_id=LEARNER,request_id=REQUEST,lesson_id='lesson_cell_membranes',lesson_version=2)
        args.update(overrides);return reserve_session_start(self.conn,**args)

    def test_reservation_is_durable_and_replays_preserve_ids_and_timestamp(self):
        first=self.reserve();before=list(self.conn.iterdump());second=self.reserve()
        self.assertTrue(first['reserved']);self.assertFalse(second['reserved'])
        self.assertEqual({k:v for k,v in first.items() if k!='reserved'},{k:v for k,v in second.items() if k!='reserved'})
        self.assertEqual(before,list(self.conn.iterdump()));self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0],0)
        for table in ('explanations','quizzes','attempts','quiz_quality_calls'):
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_changed_inputs_conflict_without_new_session(self):
        self.reserve();before=list(self.conn.iterdump())
        with self.assertRaisesRegex(StartRequestRejected,'different lesson inputs'):
            self.reserve(lesson_version=3)
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_other_learner_has_separate_request_namespace(self):
        first=self.reserve()
        other='learner_z1b2c3d4e5f6';self.conn.execute('INSERT INTO learners VALUES (?)',(other,));self.conn.commit()
        second=self.reserve(learner_id=other)
        self.assertTrue(second['reserved']);self.assertNotEqual(first['session_id'],second['session_id'])

    def test_new_request_deliberately_creates_a_new_session(self):
        first=self.reserve();second=self.reserve(request_id='request_test_0002')
        self.assertTrue(second['reserved']);self.assertNotEqual(first['session_id'],second['session_id'])

    def test_replay_reports_terminal_status_without_releasing_claim(self):
        first=self.reserve()
        self.conn.execute("UPDATE study_sessions SET status='failed' WHERE session_id=?",(first['session_id'],));self.conn.commit()
        repeated=self.reserve();self.assertEqual(repeated['status'],'failed');self.assertFalse(repeated['reserved'])

    def test_invalid_inputs_and_unapproved_version_create_no_rows(self):
        for changes in (dict(request_id='short'),dict(request_id='bad request id'),dict(lesson_version=True),
                        dict(lesson_version=99),dict(learner_id='learner_z1b2c3d4e5f6')):
            with self.subTest(changes=changes),self.assertRaises(StartRequestRejected):self.reserve(**changes)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0],0)
        self.conn.execute('UPDATE lessons SET approved=0');self.conn.commit()
        with self.assertRaises(StartRequestRejected):self.reserve()

    def test_pending_caller_work_is_not_committed_or_rolled_back(self):
        self.conn.execute("INSERT INTO learners VALUES ('pending')")
        with self.assertRaises(StartRequestRejected):self.reserve()
        self.assertTrue(self.conn.in_transaction);self.conn.rollback()
        self.assertIsNone(self.conn.execute("SELECT 1 FROM learners WHERE learner_id='pending'").fetchone())

    def test_record_insert_failure_rolls_back_session_and_handoff(self):
        self.conn.execute("CREATE TRIGGER fail_request BEFORE INSERT ON session_start_requests BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(sqlite3.Error):self.reserve()
        for table in ('study_sessions','handoff_repairs','session_start_requests'):
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        self.assertFalse(self.conn.in_transaction)

    def test_simultaneous_identical_requests_get_one_grant_and_one_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'requests.db'
            with closing(sqlite3.connect(path)) as target:self.conn.backup(target)
            barrier=threading.Barrier(2)
            def reserve(_):
                with closing(sqlite3.connect(path,timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON');barrier.wait(timeout=5)
                    return reserve_session_start(conn,LEARNER,REQUEST,'lesson_cell_membranes',2)
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,range(2)))
            self.assertCountEqual([r['reserved'] for r in results],[True,False])
            self.assertEqual(results[0]['session_id'],results[1]['session_id'])
            with closing(sqlite3.connect(path)) as conn:
                for table in ('study_sessions','handoff_repairs','session_start_requests'):
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],1)
                replay=reserve_session_start(self.enable_fk(conn),LEARNER,REQUEST,'lesson_cell_membranes',2)
                self.assertFalse(replay['reserved'])

    @staticmethod
    def enable_fk(conn):
        conn.execute('PRAGMA foreign_keys=ON');return conn

    def test_simultaneous_conflicting_inputs_have_one_winner(self):
        lesson=list(self.conn.execute('SELECT * FROM lessons').fetchone());lesson[1]=3
        self.conn.execute('INSERT INTO lessons VALUES ('+','.join('?' for _ in lesson)+')',lesson);self.conn.commit()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'conflict.db'
            with closing(sqlite3.connect(path)) as target:self.conn.backup(target)
            barrier=threading.Barrier(2)
            def reserve(version):
                with closing(sqlite3.connect(path,timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON');barrier.wait(timeout=5)
                    try:return reserve_session_start(conn,LEARNER,REQUEST,'lesson_cell_membranes',version)
                    except StartRequestRejected:return None
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,[2,3]))
            self.assertEqual(sum(result is not None for result in results),1)
            winner=next(result for result in results if result is not None)
            self.assertTrue(winner['reserved'])
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT lesson_version FROM session_start_requests').fetchone()[0],winner['lesson_version'])

    def test_foreign_keys_must_be_enabled(self):
        self.conn.execute('PRAGMA foreign_keys=OFF')
        with self.assertRaises(StartRequestRejected):self.reserve()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0],0)
