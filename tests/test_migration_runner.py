"""Atomic migration execution against isolated temporary databases."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from study_buddy.migrations import plan_migrations, MigrationTrackingRejected
from study_buddy.migration_runner import run_pending_migrations, MigrationExecutionFailed


class MigrationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'sql'
        self.directory.mkdir()
        self.database = Path(self.temp.name) / 'test.db'
        self.conn = sqlite3.connect(self.database)
        self.addCleanup(self.conn.close)
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.write(1, "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT); INSERT INTO items VALUES (1, 'a;b');")

    def write(self, version, sql):
        (self.directory / f'{version:03d}_change.sql').write_text(sql)

    def run_plan(self, fingerprint=None):
        return run_pending_migrations(self.conn, directory=self.directory,
            expected_pending_sha256=fingerprint or plan_migrations(self.conn, self.directory)['pending_sha256'])

    def assert_empty(self):
        self.assertEqual(self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
        self.assertFalse(self.conn.in_transaction)

    def test_success_and_idempotent_replay(self):
        self.write(2, 'ALTER TABLE items ADD COLUMN count INTEGER DEFAULT 0; CREATE INDEX item_value ON items(value);')
        fingerprint = plan_migrations(self.conn, self.directory)['pending_sha256']
        result = self.run_plan(fingerprint)
        self.assertEqual(result['migrations_applied'], 2)
        self.assertEqual(self.conn.execute('SELECT * FROM items').fetchall(), [(1, 'a;b', 0)])
        self.assertEqual(self.run_plan(fingerprint)['status'], 'up_to_date')
        self.assertEqual(len(plan_migrations(self.conn, self.directory)['applied']), 2)

    def test_later_sql_failure_rolls_back_entire_batch(self):
        self.write(2, 'INSERT INTO missing VALUES (1);')
        with self.assertRaises(MigrationExecutionFailed) as raised:
            self.run_plan()
        self.assertEqual(raised.exception.filename, '002_change.sql')
        self.assertEqual(raised.exception.statement_number, 1)
        self.assert_empty()

    def test_history_failure_rolls_back_ddl_and_data(self):
        with patch('study_buddy.migration_runner.record_applied_migration', side_effect=sqlite3.OperationalError('private detail')):
            with self.assertRaises(MigrationExecutionFailed) as raised:
                self.run_plan()
        self.assertEqual(raised.exception.stage, 'history recording')
        self.assertNotIn('private detail', str(raised.exception))
        self.assert_empty()

    def test_transaction_escape_and_metadata_changes_denied(self):
        for sql in ['COMMIT;', 'BEGIN;', 'SAVEPOINT escape;', 'PRAGMA foreign_keys=OFF;',
                    "ATTACH DATABASE ':memory:' AS other;", 'VACUUM;',
                    'DELETE FROM schema_migrations;', 'DROP TABLE schema_migrations;',
                    'ALTER TABLE schema_migrations RENAME TO hidden;',
                    'CREATE TRIGGER bad AFTER INSERT ON items BEGIN DELETE FROM items; END;']:
            with self.subTest(sql=sql):
                self.write(2, sql)
                with self.assertRaises(MigrationExecutionFailed):
                    self.run_plan()
                self.assert_empty()

    def test_foreign_key_violation_rolls_back(self):
        self.write(2, 'CREATE TABLE child (parent INTEGER REFERENCES items(id)); INSERT INTO child VALUES (9);')
        with self.assertRaises(MigrationExecutionFailed):
            self.run_plan()
        self.assert_empty()

    def test_stale_plan_rejected(self):
        fingerprint = plan_migrations(self.conn, self.directory)['pending_sha256']
        self.write(2, 'CREATE TABLE another (id INTEGER);')
        with self.assertRaises(MigrationTrackingRejected):
            self.run_plan(fingerprint)
        self.assert_empty()

    def test_untracked_existing_schema_requires_baseline(self):
        self.conn.execute('CREATE TABLE existing (id INTEGER)')
        with self.assertRaises(MigrationTrackingRejected):
            self.run_plan()
        self.assertEqual(self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [('existing',)])

    def test_pending_work_is_not_committed_or_rolled_back(self):
        self.conn.execute('CREATE TABLE existing (id INTEGER)')
        self.conn.execute('INSERT INTO existing VALUES (1)')
        with self.assertRaises(MigrationTrackingRejected):
            self.run_plan()
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT * FROM existing').fetchall(), [])

    def test_requires_foreign_keys_and_reviewed_fingerprint(self):
        self.conn.execute('PRAGMA foreign_keys=OFF')
        with self.assertRaises(MigrationTrackingRejected):
            self.run_plan()
        self.conn.execute('PRAGMA foreign_keys=ON')
        with self.assertRaises(MigrationTrackingRejected):
            self.run_plan('invalid')
        self.assert_empty()

    def test_recorded_file_drift_is_rejected(self):
        self.run_plan()
        self.write(1, 'CREATE TABLE changed (id INTEGER);')
        with self.assertRaises(MigrationTrackingRejected):
            self.run_plan('0' * 64)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM items').fetchone()[0], 1)

    def test_cli_opt_in_backup_and_no_overwrite(self):
        fingerprint = plan_migrations(self.conn, self.directory)['pending_sha256']
        backup = Path(self.temp.name) / 'backup.db'
        command = [sys.executable, '-B', 'scripts/migrate_database.py', str(self.database),
                   '--pending-sha256', fingerprint, '--backup', str(backup),
                   '--migrations-dir', str(self.directory)]
        denied = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(denied.returncode, 0)
        self.assertFalse(backup.exists())
        result = subprocess.run(command + ['--apply'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['migrations_applied'], 1)
        self.assertEqual(os.stat(backup).st_mode & 0o777, 0o600)
        with sqlite3.connect(backup) as saved:
            self.assertEqual(saved.execute('SELECT name FROM sqlite_master').fetchall(), [])
        refused = subprocess.run(command + ['--apply'], capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)

    def test_baselined_application_accepts_only_new_migration(self):
        import shutil
        from study_buddy.migrations import DEFAULT_MIGRATIONS
        from study_buddy.migration_baseline import plan_baseline, record_verified_baseline
        root = DEFAULT_MIGRATIONS.parent.parent
        self.conn.executescript((root / 'db/baselines/008_schema.sql').read_text())
        preview = plan_baseline(self.conn, through_version=8)
        record_verified_baseline(self.conn, through_version=8,
            expected_schema_sha256=preview['schema_sha256'],
            expected_manifest_sha256=preview['manifest_sha256'],
            recorded_by='test operator', reason='Verified test schema')
        for file in self.directory.iterdir():
            file.unlink()
        for file in DEFAULT_MIGRATIONS.glob('*.sql'):
            shutil.copyfile(file, self.directory / file.name)
        self.write(11, 'CREATE TABLE maintenance_probe (id INTEGER PRIMARY KEY);')
        result = self.run_plan()
        self.assertEqual([row['version'] for row in result['applied']], [9, 10, 11])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM schema_baselines').fetchone()[0], 1)
        self.assertEqual(len(plan_migrations(self.conn, self.directory)['applied']), 11)
