"""Exercise the worker boundary using a real disposable application database."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests import test_quality_expiry as fixtures
from study_buddy.maintenance_worker import run_maintenance_cycle


class MaintenanceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'practice.db'
        self.helper = fixtures.QualityExpiryTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.helper.expire()
        with closing(sqlite3.connect(self.database)) as target:
            self.helper.conn.backup(target)

    def test_one_cycle_expires_saved_work_and_replay_is_empty(self):
        result = run_maintenance_cycle(self.database, limit=1)
        self.assertEqual(result, dict(operations_checked=1, operations_expired=1,
            calls_failed=1, sessions_failed=1, retry_operations_failed=0))
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT stage,failure_code FROM quiz_quality_operations').fetchone(),
                             ('failed', 'quality_deadline'))
            self.assertEqual(conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'failed')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM quiz_quality_calls').fetchone()[0], 1)
        self.assertTrue(all(value == 0 for value in run_maintenance_cycle(self.database).values()))

    def test_unexpired_work_is_preserved(self):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute('UPDATE quiz_quality_operations SET started_at=4102444700, deadline_at=4102444800')
            conn.commit()
            before = list(conn.iterdump())
        self.assertEqual(run_maintenance_cycle(self.database)['operations_checked'], 0)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(list(conn.iterdump()), before)

    def test_missing_database_is_not_created(self):
        missing = Path(self.temp.name) / 'missing.db'
        with self.assertRaises(sqlite3.OperationalError):
            run_maintenance_cycle(missing)
        self.assertFalse(missing.exists())

    def test_invalid_limit_fails_before_opening_connection(self):
        for limit in (0, 1001, True, 1.5, '10'):
            with self.subTest(limit=limit), patch('study_buddy.maintenance_worker.sqlite3.connect') as connect:
                with self.assertRaises(ValueError):
                    run_maintenance_cycle(self.database, limit=limit)
                connect.assert_not_called()

    def test_database_failure_propagates_and_closes_owned_connection(self):
        captured = []
        def fail(conn, **kwargs):
            captured.append(conn)
            self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            raise sqlite3.OperationalError('simulated database failure')
        with patch('study_buddy.maintenance_worker.sweep_expired_quality_operations', side_effect=fail) as sweep:
            with self.assertRaises(sqlite3.OperationalError):
                run_maintenance_cycle(self.database)
            self.assertEqual(sweep.call_count, 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            captured[0].execute('SELECT 1')
        # Failure has not been reported as success or silently retried.
        self.assertEqual(run_maintenance_cycle(self.database)['operations_expired'], 1)
