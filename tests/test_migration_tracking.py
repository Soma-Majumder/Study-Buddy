"""Migration metadata only; no application migration runner yet."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from study_buddy.migrations import (initialize_migration_tracking, list_applied_migrations,
                                   record_applied_migration, MigrationTrackingRejected)

ROOT = Path(__file__).resolve().parent.parent
NAME = '001_feedback_operations.sql'
SQL = (ROOT / 'db/migrations' / NAME).read_bytes()


class MigrationTrackingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        initialize_migration_tracking(self.conn)

    def test_initialization_is_idempotent_and_never_infers_applied_versions(self):
        self.conn.execute('CREATE TABLE existing_data (value TEXT)')
        self.conn.execute("INSERT INTO existing_data VALUES ('keep')"); self.conn.commit()
        initialize_migration_tracking(self.conn)
        self.assertEqual(list_applied_migrations(self.conn), [])
        self.assertEqual(self.conn.execute('SELECT value FROM existing_data').fetchone()[0], 'keep')

    def test_records_exact_bytes_timestamp_and_replays_without_changing_record(self):
        first = record_applied_migration(self.conn, NAME, SQL)
        self.assertTrue(first['recorded'])
        self.assertEqual(first['version'], 1)
        self.assertEqual(first['sha256'], hashlib.sha256(SQL).hexdigest())
        self.assertRegex(first['applied_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$')
        before = list(self.conn.iterdump())
        replay = record_applied_migration(self.conn, NAME, SQL)
        self.assertEqual(replay, dict(first, recorded=False))
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_rejects_changed_sql_even_when_only_whitespace_changed(self):
        record_applied_migration(self.conn, NAME, SQL)
        before = list(self.conn.iterdump())
        with self.assertRaises(MigrationTrackingRejected): record_applied_migration(self.conn, NAME, SQL+b'\n')
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_rejects_renaming_a_recorded_version(self):
        record_applied_migration(self.conn, NAME, SQL)
        with self.assertRaises(MigrationTrackingRejected):
            record_applied_migration(self.conn, '001_renamed.sql', SQL)
        self.assertEqual(len(list_applied_migrations(self.conn)), 1)

    def test_lists_in_numeric_version_order(self):
        record_applied_migration(self.conn, '008_quiz_quality_inputs.sql', b'-- fixture 8')
        record_applied_migration(self.conn, NAME, SQL)
        self.assertEqual([r['version'] for r in list_applied_migrations(self.conn)], [1, 8])

    def test_invalid_inputs_do_not_write_metadata(self):
        for filename, data in [('../001_bad.sql', SQL), ('000_zero.sql', SQL), ('1_bad.sql', SQL),
                               ('001_bad.SQL', SQL), ('001_bad-name.sql', SQL), (None, SQL),
                               (NAME, ''), (NAME, b''), (NAME, b'   '), ('001_bad.sql\n', SQL)]:
            with self.subTest(filename=filename):
                with self.assertRaises(MigrationTrackingRejected):record_applied_migration(self.conn, filename, data)
        self.assertEqual(list_applied_migrations(self.conn), [])

    def test_tracking_never_executes_supplied_migration_sql(self):
        record_applied_migration(self.conn, '009_not_executed.sql', b'CREATE TABLE should_not_exist (value TEXT);')
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='should_not_exist'").fetchone())

    def test_record_can_share_and_roll_back_a_callers_transaction(self):
        with self.assertRaises(RuntimeError):
            with self.conn:
                self.conn.execute('CREATE TABLE sample (value TEXT)')
                self.conn.execute("INSERT INTO sample VALUES ('rollback')")
                record_applied_migration(self.conn, NAME, SQL, manage_transaction=False)
                raise RuntimeError('simulated migration failure')
        self.assertEqual(list_applied_migrations(self.conn), [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sample').fetchone()[0], 0)

    def test_initialization_does_not_commit_pending_application_writes(self):
        self.conn.execute('CREATE TABLE sample (value TEXT)')
        self.conn.execute("INSERT INTO sample VALUES ('pending')")
        initialize_migration_tracking(self.conn)
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sample').fetchone()[0], 0)

    def test_metadata_persists_after_reopening_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'study.db'
            with closing(sqlite3.connect(path)) as conn:
                initialize_migration_tracking(conn);record_applied_migration(conn, NAME, SQL)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(list_applied_migrations(conn)[0]['sha256'], hashlib.sha256(SQL).hexdigest())

    def test_concurrent_recording_creates_one_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'study.db'
            with closing(sqlite3.connect(path)) as conn:initialize_migration_tracking(conn)
            def record():
                with closing(sqlite3.connect(path, timeout=5)) as conn:
                    return record_applied_migration(conn, NAME, SQL)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _:record(), range(2)))
            self.assertEqual(sum(r['recorded'] for r in results), 1)
            with closing(sqlite3.connect(path)) as conn:self.assertEqual(len(list_applied_migrations(conn)), 1)

    def test_failed_metadata_insert_does_not_claim_recorded(self):
        self.conn.execute("CREATE TRIGGER fail_tracking BEFORE INSERT ON schema_migrations BEGIN SELECT RAISE(ABORT,'save failure'); END")
        with self.assertRaises(sqlite3.Error):record_applied_migration(self.conn, NAME, SQL)
        self.assertEqual(list_applied_migrations(self.conn), [])
