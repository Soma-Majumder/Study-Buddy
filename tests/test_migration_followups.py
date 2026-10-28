"""Issue #42 follow-ups: versioned baselines, backup ordering, init and metadata protection."""
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from study_buddy import migration_baseline
from study_buddy.migration_backup import write_backup
from study_buddy.migration_baseline import baseline_references, plan_baseline, record_verified_baseline
from study_buddy.migration_runner import run_pending_migrations, MigrationExecutionFailed
from study_buddy.migrations import plan_migrations, MigrationTrackingRejected

ROOT = Path(__file__).resolve().parent.parent


class NextMigrationBaselineTests(unittest.TestCase):
    """Simulate adding migration 011 without editing any code."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.migrations = base/'migrations'; shutil.copytree(ROOT/'db/migrations', self.migrations)
        (self.migrations/'011_extra.sql').write_text('CREATE TABLE extra (id INTEGER PRIMARY KEY);')
        self.baselines = base/'baselines'; shutil.copytree(ROOT/'db/baselines', self.baselines)
        self.old_current = (ROOT/'db/schema.sql').read_text()
        shutil.copy(ROOT/'db/schema.sql', self.baselines/'010_schema.sql')  # freeze before changing schema.sql
        self.new_current = base/'schema.sql'
        self.new_current.write_text(self.old_current + '\nCREATE TABLE extra (id INTEGER PRIMARY KEY);\n')
        for name, value in (('MIGRATIONS', self.migrations), ('BASELINES', self.baselines), ('CURRENT_SCHEMA', self.new_current)):
            patcher = patch.object(migration_baseline, name, value); patcher.start(); self.addCleanup(patcher.stop)

    def database(self, sql):
        conn = sqlite3.connect(':memory:'); self.addCleanup(conn.close); conn.executescript(sql); return conn

    def test_references_follow_the_newest_migration(self):
        self.assertEqual(sorted(baseline_references(11)), [8, 9, 10, 11])
        self.assertEqual(baseline_references(11)[11], self.new_current)

    def test_older_databases_can_still_be_baselined_after_the_schema_changes(self):
        for version in (8, 9, 10):
            conn = self.database((self.baselines/f'{version:03d}_schema.sql').read_text())
            result = plan_baseline(conn, through_version=version)
            self.assertEqual((result['status'], len(result['would_record'])), ('baseline_ready', version))

    def test_new_database_baselines_at_the_newest_version_only(self):
        conn = self.database(self.new_current.read_text())
        self.assertEqual(plan_baseline(conn, through_version=11)['status'], 'baseline_ready')
        for version in (8, 9, 10):
            with self.assertRaises(MigrationTrackingRejected):
                plan_baseline(conn, through_version=version)
        with self.assertRaises(MigrationTrackingRejected):
            plan_baseline(self.database(self.old_current), through_version=11)

    def test_missing_frozen_snapshot_is_rejected_not_guessed(self):
        (self.baselines/'010_schema.sql').unlink()
        with self.assertRaises(MigrationTrackingRejected):
            plan_baseline(self.database(self.old_current), through_version=10)

    def test_old_database_can_be_recorded_then_takes_the_new_migration(self):
        conn = self.database((self.baselines/'010_schema.sql').read_text())
        preview = plan_baseline(conn, through_version=10)
        record_verified_baseline(conn, through_version=10, expected_schema_sha256=preview['schema_sha256'],
            expected_manifest_sha256=preview['manifest_sha256'], recorded_by='test', reason='old schema')
        conn.execute('PRAGMA foreign_keys=ON')
        plan = plan_migrations(conn, self.migrations)
        self.assertEqual([m['version'] for m in plan['pending']], [11])
        result = run_pending_migrations(conn, expected_pending_sha256=plan['pending_sha256'], directory=self.migrations)
        self.assertEqual(result['migrations_applied'], 1)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM extra').fetchone()[0], 0)

    def test_repository_supports_the_historical_and_current_versions(self):
        with patch.object(migration_baseline, 'MIGRATIONS', ROOT/'db/migrations'), \
             patch.object(migration_baseline, 'BASELINES', ROOT/'db/baselines'), \
             patch.object(migration_baseline, 'CURRENT_SCHEMA', ROOT/'db/schema.sql'):
            self.assertEqual(sorted(baseline_references(10)), [8, 9, 10])


class RunnerBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)/'sql'; self.directory.mkdir()
        self.database = Path(self.temp.name)/'test.db'
        self.conn = sqlite3.connect(self.database); self.addCleanup(self.conn.close)
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.write(1, "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT); INSERT INTO items VALUES (1, 'a');")

    def write(self, version, sql):
        (self.directory/f'{version:03d}_change.sql').write_text(sql)

    def fingerprint(self):
        return plan_migrations(self.conn, self.directory)['pending_sha256']

    def run_plan(self, **kwargs):
        return run_pending_migrations(self.conn, directory=self.directory, expected_pending_sha256=self.fingerprint(), **kwargs)


class BackupOrderingTests(RunnerBase):
    def test_backup_runs_under_the_write_lock_and_is_complete(self):
        backup = Path(self.temp.name)/'backup.db'
        seen = {}

        def backup_under_lock(conn):
            seen['in_transaction'] = conn.in_transaction
            with closing(sqlite3.connect(self.database, timeout=0)) as writer:
                try:
                    writer.execute('CREATE TABLE sneaked (id INTEGER)')
                except sqlite3.OperationalError as error:
                    seen['writer'] = str(error)
            write_backup(conn, backup)

        self.run_plan(before_apply=backup_under_lock)
        self.assertTrue(seen['in_transaction'])
        self.assertIn('locked', seen['writer'])
        with closing(sqlite3.connect(backup)) as saved:
            self.assertEqual(saved.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])

    def test_no_backup_when_the_plan_is_rejected_or_nothing_is_pending(self):
        calls = []
        with self.assertRaises(MigrationTrackingRejected):
            run_pending_migrations(self.conn, directory=self.directory, expected_pending_sha256='0'*64, before_apply=calls.append)
        self.run_plan()
        self.run_plan(before_apply=calls.append)
        self.assertEqual(calls, [])

    def test_failed_backup_rolls_back_and_leaves_no_file(self):
        backup = Path(self.temp.name)/'backup.db'

        with patch('study_buddy.migration_backup._copy', side_effect=sqlite3.OperationalError('disk full')), \
             self.assertRaises(MigrationExecutionFailed) as raised:
            self.run_plan(before_apply=lambda conn: write_backup(conn, backup))
        self.assertEqual(raised.exception.stage, 'backup')
        self.assertFalse(backup.exists())
        self.assertEqual(self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
        self.assertFalse(self.conn.in_transaction)

    def test_partial_backup_is_removed_but_an_existing_path_is_untouched(self):
        backup = Path(self.temp.name)/'partial.db'
        with patch('study_buddy.migration_backup._copy', side_effect=sqlite3.OperationalError('disk full')), \
             self.assertRaises(sqlite3.OperationalError):
            write_backup(self.conn, backup)
        self.assertFalse(backup.exists())
        existing = Path(self.temp.name)/'existing.db'
        existing.write_text('keep me')
        with self.assertRaises(FileExistsError):
            write_backup(self.conn, existing)
        self.assertEqual(existing.read_text(), 'keep me')

    def test_cli_rejection_leaves_no_backup_and_success_writes_one(self):
        backup = Path(self.temp.name)/'cli-backup.db'
        command = [sys.executable, '-B', 'scripts/migrate_database.py', str(self.database), '--apply',
                   '--backup', str(backup), '--migrations-dir', str(self.directory), '--pending-sha256']
        wrong = subprocess.run(command + ['0'*64], capture_output=True, text=True, cwd=ROOT)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertFalse(backup.exists())
        ok = subprocess.run(command + [self.fingerprint()], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(os.stat(backup).st_mode & 0o777, 0o600)


class MetadataProtectionTests(RunnerBase):
    def test_renaming_another_table_onto_a_metadata_name_is_rejected(self):
        self.run_plan()
        self.write(2, 'CREATE TABLE b (x INTEGER); ALTER TABLE b RENAME TO schema_baselines;')
        with self.assertRaises(MigrationExecutionFailed) as raised:
            self.run_plan()
        self.assertEqual(raised.exception.stage, 'metadata protection')
        names = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {'items', 'schema_migrations'})


class InitDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'new.db'

    def run_init(self, path):
        return subprocess.run([sys.executable, '-B', 'scripts/init_database.py', str(path)],
                              capture_output=True, text=True, cwd=ROOT)

    def test_creates_a_baselinable_private_database_and_never_overwrites(self):
        result = self.run_init(self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(plan_baseline(conn, through_version=10)['status'], 'baseline_ready')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM learners').fetchone()[0], 0)
        before = self.path.read_bytes()
        again = self.run_init(self.path)
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_refuses_a_symlink_and_leaves_its_target(self):
        target = Path(self.temp.name)/'target.db'; target.write_text('keep')
        link = Path(self.temp.name)/'link.db'; link.symlink_to(target)
        self.assertNotEqual(self.run_init(link).returncode, 0)
        self.assertEqual(target.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
