"""Exercise the worker boundary using a real disposable application database."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock

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


class MaintenanceLoopTests(unittest.TestCase):
    def test_repeats_serially_and_stop_during_cycle_skips_wait(self):
        from threading import Event
        from study_buddy.maintenance_worker import run_maintenance_worker
        stop = Event()
        received = []
        def cycle(database, **kwargs):
            if len(received) == 1:
                stop.set()
            return {'operations_checked': 0}
        with patch('study_buddy.maintenance_worker.run_maintenance_cycle', side_effect=cycle) as call:
            result = run_maintenance_worker('unused', interval_seconds=0.001,
                stop_event=stop, on_cycle=received.append)
        self.assertEqual(result, dict(status='stopped', cycles_completed=2))
        self.assertEqual(call.call_count, 2)
        self.assertEqual(len(received), 2)

    def test_stop_before_start_does_not_open_database(self):
        from threading import Event
        from study_buddy.maintenance_worker import run_maintenance_worker
        stop = Event(); stop.set()
        with patch('study_buddy.maintenance_worker.run_maintenance_cycle') as cycle:
            self.assertEqual(run_maintenance_worker('unused', stop_event=stop)['cycles_completed'], 0)
            cycle.assert_not_called()

    def test_wait_is_interruptible_and_does_not_start_another_cycle(self):
        from study_buddy.maintenance_worker import run_maintenance_worker
        stop = unittest.mock.Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        with patch('study_buddy.maintenance_worker.run_maintenance_cycle', return_value={}) as cycle:
            result = run_maintenance_worker('unused', stop_event=stop, interval_seconds=300)
        self.assertEqual(result['cycles_completed'], 1)
        stop.wait.assert_called_once_with(300)
        cycle.assert_called_once()

    def test_cycle_failure_stops_without_success_callback_or_retry(self):
        from threading import Event
        from study_buddy.maintenance_worker import run_maintenance_worker
        callback = unittest.mock.Mock()
        stop = Event()
        with patch('study_buddy.maintenance_worker.run_maintenance_cycle', side_effect=sqlite3.OperationalError('failure')) as cycle:
            with self.assertRaises(sqlite3.OperationalError):
                run_maintenance_worker('unused', stop_event=stop, on_cycle=callback)
            cycle.assert_called_once()
        callback.assert_not_called()

    def test_invalid_intervals_fail_before_cycle(self):
        from study_buddy.maintenance_worker import run_maintenance_worker
        for interval in (0, -1, True, float('nan'), float('inf'), 86401, '30'):
            with self.subTest(interval=interval), patch('study_buddy.maintenance_worker.run_maintenance_cycle') as cycle:
                with self.assertRaises(ValueError):
                    run_maintenance_worker('unused', interval_seconds=interval)
                cycle.assert_not_called()

    def test_cli_signal_shutdown_during_wait(self):
        import json
        import select
        import signal
        import subprocess
        import sys
        fixture = MaintenanceWorkerTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum):
                process = subprocess.Popen([sys.executable, '-B', 'scripts/run_maintenance_worker.py',
                    str(fixture.database), '--interval', '300'], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                try:
                    ready, _, _ = select.select([process.stdout], [], [], 10)
                    self.assertTrue(ready, 'Worker did not report its first cycle')
                    self.assertEqual(json.loads(process.stdout.readline())['event'], 'cycle_completed')
                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 0, stderr)
                    self.assertEqual(json.loads(stdout)['cycles_completed'], 1)
                    self.assertEqual(json.loads(stdout)['event'], 'worker_stopped')
                finally:
                    if process.poll() is None:
                        process.kill(); process.communicate()
                    process.stdout.close(); process.stderr.close()


class MaintenanceRetryTests(unittest.TestCase):
    @staticmethod
    def locked(code=sqlite3.SQLITE_BUSY):
        error = sqlite3.OperationalError('intentionally irrelevant text')
        error.sqlite_errorcode = code
        return error

    def test_transient_lock_then_success_resets_retry_budget_each_cycle(self):
        from study_buddy.maintenance_worker import run_maintenance_worker
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, False, False, True]
        retries, completed = [], []
        with patch('study_buddy.maintenance_worker.run_maintenance_cycle',
                   side_effect=[self.locked(), {}, self.locked(sqlite3.SQLITE_LOCKED), {}]) as cycle:
            result = run_maintenance_worker('unused', stop_event=stop,
                on_cycle=completed.append, on_retry=retries.append)
        self.assertEqual(cycle.call_count, 4)
        self.assertEqual(result['cycles_completed'], 2)
        self.assertEqual([row['retry_number'] for row in retries], [1, 1])
        self.assertEqual(len(completed), 2)

    def test_retry_exhaustion_is_bounded_with_backoff(self):
        from study_buddy.maintenance_worker import run_maintenance_worker, MaintenanceLockRetriesExhausted
        stop = Mock(); stop.is_set.return_value = False; stop.wait.return_value = False
        callback = Mock()
        with patch('study_buddy.maintenance_worker.run_maintenance_cycle', side_effect=self.locked(sqlite3.SQLITE_BUSY | (2 << 8))) as cycle:
            with self.assertRaises(MaintenanceLockRetriesExhausted):
                run_maintenance_worker('unused', stop_event=stop, on_cycle=callback)
        self.assertEqual(cycle.call_count, 4)
        self.assertEqual(stop.wait.call_args_list, [unittest.mock.call(1), unittest.mock.call(2), unittest.mock.call(4)])
        callback.assert_not_called()

    def test_shutdown_interrupts_retry_wait_without_another_attempt(self):
        from study_buddy.maintenance_worker import run_maintenance_worker
        stop = Mock(); stop.is_set.return_value = False; stop.wait.return_value = True
        with patch('study_buddy.maintenance_worker.run_maintenance_cycle', side_effect=self.locked()) as cycle:
            self.assertEqual(run_maintenance_worker('unused', stop_event=stop)['cycles_completed'], 0)
        cycle.assert_called_once()

    def test_message_alone_does_not_authorize_retry(self):
        from study_buddy.maintenance_worker import run_maintenance_worker
        for error in (sqlite3.OperationalError('database is locked'), ValueError('sweep bug')):
            with self.subTest(error=error), patch('study_buddy.maintenance_worker.run_maintenance_cycle', side_effect=error) as cycle:
                with self.assertRaises(type(error)):
                    run_maintenance_worker('unused')
                cycle.assert_called_once()

    def test_cli_distinguishes_configuration_sweep_and_exhaustion(self):
        import importlib.util
        from study_buddy.maintenance_worker import WorkerConfigurationError, MaintenanceLockRetriesExhausted
        path = Path(__file__).resolve().parent.parent / 'scripts/run_maintenance_worker.py'
        spec = importlib.util.spec_from_file_location('worker_cli_for_test', path)
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        for error, code, expected in ((WorkerConfigurationError('bad limit'), 2, 'Invalid worker configuration'),
                                      (ValueError('sweep bug'), 1, 'Maintenance worker failed'),
                                      (MaintenanceLockRetriesExhausted(), 75, 'temporary database lock retries')):
            import io
            with self.subTest(error=error), patch('sys.argv', ['worker', 'unused']), patch.object(cli, 'run_maintenance_worker', side_effect=error), patch('sys.stderr', new_callable=io.StringIO) as stderr:
                with self.assertRaises(SystemExit) as raised:
                    cli.main()
                self.assertEqual(raised.exception.code, code)
                self.assertIn(expected, stderr.getvalue())
                if code != 2:
                    self.assertNotIn('Invalid worker configuration', stderr.getvalue())

    def test_real_sqlite_write_lock_recovers_with_a_fresh_cycle(self):
        from threading import Event
        from study_buddy.maintenance_worker import run_maintenance_worker
        fixture = MaintenanceWorkerTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        real_connect = sqlite3.connect
        stop = Event()
        completed, retries = [], []
        with closing(real_connect(fixture.database)) as locker:
            locker.execute('BEGIN IMMEDIATE')
            def quick_connect(*args, **kwargs):
                kwargs['timeout'] = 0.01
                return real_connect(*args, **kwargs)
            def release_lock(info):
                retries.append(info)
                locker.rollback()
            def finish(counts):
                completed.append(counts)
                stop.set()
            with patch('study_buddy.maintenance_worker.sqlite3.connect', side_effect=quick_connect):
                result = run_maintenance_worker(fixture.database, stop_event=stop,
                    on_retry=release_lock, on_cycle=finish)
        self.assertEqual(result['cycles_completed'], 1)
        self.assertEqual(len(retries), 1)
        self.assertEqual(completed[0]['operations_expired'], 1)
