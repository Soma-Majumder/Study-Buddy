"""Read-only file discovery and history comparison; never apply SQL."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from study_buddy.migrations import (discover_migrations, plan_migrations, initialize_migration_tracking,
                                   record_applied_migration, MigrationTrackingRejected)

ROOT = Path(__file__).resolve().parent.parent


class MigrationDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:'); self.addCleanup(self.conn.close)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        # Reverse filesystem creation order deliberately.
        (self.directory/'002_second.sql').write_bytes(b'CREATE TABLE second (value TEXT);\n')
        (self.directory/'001_first.sql').write_bytes(b'CREATE TABLE first (value TEXT);\n')
        (self.directory/'README.md').write_text('Not SQL.')

    def record(self, version=1):
        initialize_migration_tracking(self.conn)
        migration = discover_migrations(self.directory)[version-1]
        record_applied_migration(self.conn, migration['filename'], migration['sql_bytes'])

    def test_repo_files_discovered_in_order_with_exact_fingerprints(self):
        files = discover_migrations()
        self.assertEqual([m['version'] for m in files], list(range(1,11)))
        self.assertEqual(files[-1]['filename'], '010_agent_operation_deadlines.sql')
        for m in files:
            self.assertEqual(m['sha256'], hashlib.sha256(m['sql_bytes']).hexdigest())

    def test_uninitialized_empty_database_reports_pending_without_creating_tracking(self):
        before = list(self.conn.iterdump())
        plan = plan_migrations(self.conn, self.directory)
        self.assertEqual(plan['status'], 'pending'); self.assertFalse(plan['tracking_initialized'])
        self.assertEqual([m['version'] for m in plan['pending']], [1,2])
        self.assertNotIn('sql_bytes', json.dumps(plan))
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_only_unrecorded_suffix_is_pending_and_all_recorded_is_up_to_date(self):
        self.record()
        plan = plan_migrations(self.conn, self.directory)
        self.assertEqual([m['version'] for m in plan['applied']], [1])
        self.assertEqual([m['version'] for m in plan['pending']], [2])
        self.record(2)
        self.assertEqual(plan_migrations(self.conn, self.directory)['status'], 'up_to_date')

    def test_fresh_application_schema_without_history_requires_baseline(self):
        self.conn.executescript((ROOT/'db/schema.sql').read_text())
        before = list(self.conn.iterdump())
        plan = plan_migrations(self.conn)
        self.assertEqual(plan['status'], 'baseline_required'); self.assertTrue(plan['baseline_required'])
        self.assertEqual(len(plan['pending']), 10)  # Unrecorded does not mean unapplied.
        self.assertEqual(plan['applied'], [])
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_empty_tracking_table_does_not_hide_baseline_requirement(self):
        initialize_migration_tracking(self.conn)
        self.conn.execute('CREATE TABLE existing_data (value TEXT)')
        plan = plan_migrations(self.conn, self.directory)
        self.assertTrue(plan['tracking_initialized']); self.assertTrue(plan['baseline_required'])

    def test_edited_recorded_file_and_renamed_recorded_version_are_rejected(self):
        self.record()
        path = self.directory/'001_first.sql'; original = path.read_bytes()
        path.write_bytes(original+b'\n')
        with self.assertRaises(MigrationTrackingRejected): plan_migrations(self.conn, self.directory)
        path.write_bytes(original); path.rename(self.directory/'001_renamed.sql')
        with self.assertRaises(MigrationTrackingRejected): plan_migrations(self.conn, self.directory)

    def test_recorded_file_missing_and_nonprefix_history_are_rejected(self):
        self.record(2)
        with self.assertRaises(MigrationTrackingRejected): plan_migrations(self.conn, self.directory)
        (self.directory/'002_second.sql').unlink()
        with self.assertRaises(MigrationTrackingRejected): plan_migrations(self.conn, self.directory)

    def test_duplicate_versions_gaps_and_sql_looking_invalid_names_rejected(self):
        for filename in ('001_duplicate.sql', '003_gap.SQL', 'bad.sql', '000_zero.sql'):
            with self.subTest(filename=filename):
                path = self.directory/filename; path.write_text('-- SQL fixture')
                with self.assertRaises(MigrationTrackingRejected): discover_migrations(self.directory)
                path.unlink()
        (self.directory/'001_first.sql').unlink()
        with self.assertRaises(MigrationTrackingRejected): discover_migrations(self.directory)

    def test_empty_non_utf8_and_symlink_sql_files_are_rejected(self):
        path = self.directory/'001_first.sql'
        for data in (b'', b' \n', b'\xff'):
            path.write_bytes(data)
            with self.assertRaises(MigrationTrackingRejected): discover_migrations(self.directory)
        path.unlink(); path.symlink_to(self.directory/'002_second.sql')
        with self.assertRaises(MigrationTrackingRejected): discover_migrations(self.directory)

    def test_missing_directory_and_empty_directory(self):
        with self.assertRaises(MigrationTrackingRejected): discover_migrations(self.directory/'missing')
        for path in self.directory.iterdir(): path.unlink()
        self.assertEqual(discover_migrations(self.directory), [])
        self.assertEqual(plan_migrations(self.conn, self.directory)['status'], 'up_to_date')

    def test_planning_preserves_pending_caller_transaction(self):
        self.conn.execute('CREATE TABLE sample (value TEXT)')
        self.conn.execute("INSERT INTO sample VALUES ('pending')")
        self.assertTrue(self.conn.in_transaction)
        plan_migrations(self.conn, self.directory)
        self.assertTrue(self.conn.in_transaction); self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sample').fetchone()[0], 0)

    def test_status_command_opens_read_only_and_never_creates_missing_database(self):
        path = self.directory/'existing.db'
        with closing(sqlite3.connect(path)) as conn: conn.execute('CREATE TABLE existing_data (value TEXT)')
        before = path.read_bytes()
        result = subprocess.run([sys.executable, '-B', str(ROOT/'scripts/migration_status.py'), str(path)],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'baseline_required')
        self.assertEqual(before, path.read_bytes())
        missing = self.directory/'missing.db'
        result = subprocess.run([sys.executable, '-B', str(ROOT/'scripts/migration_status.py'), str(missing)],
                                text=True, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0); self.assertFalse(missing.exists())
