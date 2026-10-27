"""Migration 009 upgrades verified 008 and matches fresh schema without data loss."""
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from study_buddy.migration_baseline import plan_baseline, record_verified_baseline
from study_buddy.migration_runner import run_pending_migrations, MigrationExecutionFailed
from study_buddy.migrations import plan_migrations, MigrationTrackingRejected
from study_buddy.session_start_requests import reserve_session_start
from study_buddy.lessons import load_lessons

ROOT=Path(__file__).resolve().parent.parent
LEARNER='learner_a1b2c3d4e5f6'


class SessionStartMigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.addCleanup(self.conn.close)
        self.conn.executescript((ROOT/'db/baselines/008_schema.sql').read_text())
        load_lessons(self.conn)
        self.conn.execute('INSERT INTO learners VALUES (?)',(LEARNER,));self.conn.commit()

    def baseline(self,conn,version):
        plan=plan_baseline(conn,through_version=version)
        return record_verified_baseline(conn,through_version=version,
            expected_schema_sha256=plan['schema_sha256'],expected_manifest_sha256=plan['manifest_sha256'],
            recorded_by='migration test',reason='Disposable schema verification')

    def test_008_baseline_records_only_eight_then_009_preserves_prior_rows(self):
        self.assertEqual(self.baseline(self.conn,8)['records_written'],8)
        before=self.conn.execute('SELECT * FROM lessons').fetchall()
        plan=plan_migrations(self.conn)
        self.assertEqual([m['version'] for m in plan['pending']],[9])
        result=run_pending_migrations(self.conn,expected_pending_sha256=plan['pending_sha256'])
        self.assertEqual(result['migrations_applied'],1)
        self.assertEqual(self.conn.execute('SELECT * FROM lessons').fetchall(),before)
        self.assertEqual(self.conn.execute('SELECT learner_id FROM learners').fetchall(),[(LEARNER,)])
        self.assertEqual(plan_migrations(self.conn)['status'],'up_to_date')
        self.assertEqual(self.conn.execute('SELECT through_version FROM schema_baselines').fetchone()[0],8)
        claim=reserve_session_start(self.conn,LEARNER,'migration_request_01','lesson_cell_membranes',2)
        self.assertTrue(claim['reserved'])
        self.assertFalse(reserve_session_start(self.conn,LEARNER,'migration_request_01','lesson_cell_membranes',2)['reserved'])

    def test_fresh_009_baseline_records_nine_and_has_no_pending_sql(self):
        with closing(sqlite3.connect(':memory:')) as fresh:
            fresh.executescript((ROOT/'db/schema.sql').read_text())
            self.assertEqual(self.baseline(fresh,9)['records_written'],9)
            self.assertEqual(plan_migrations(fresh)['status'],'up_to_date')
            self.assertEqual(fresh.execute('SELECT through_version FROM schema_baselines').fetchone()[0],9)

    def test_baseline_never_claims_wrong_schema_version(self):
        with self.assertRaises(MigrationTrackingRejected):plan_baseline(self.conn,through_version=9)
        with closing(sqlite3.connect(':memory:')) as fresh:
            fresh.executescript((ROOT/'db/schema.sql').read_text())
            with self.assertRaises(MigrationTrackingRejected):plan_baseline(fresh,through_version=8)

    def test_upgraded_table_matches_fresh_schema(self):
        self.baseline(self.conn,8);plan=plan_migrations(self.conn)
        run_pending_migrations(self.conn,expected_pending_sha256=plan['pending_sha256'])
        with closing(sqlite3.connect(':memory:')) as fresh:
            fresh.executescript((ROOT/'db/schema.sql').read_text())
            query="SELECT sql FROM sqlite_master WHERE name='session_start_requests'"
            self.assertEqual(self.conn.execute(query).fetchone(),fresh.execute(query).fetchone())

    def test_history_save_failure_rolls_back_009_table_and_keeps_008_history(self):
        self.baseline(self.conn,8);plan=plan_migrations(self.conn);before=list(self.conn.iterdump())
        with patch('study_buddy.migration_runner.record_applied_migration',side_effect=sqlite3.OperationalError('fixture failure')):
            with self.assertRaises(MigrationExecutionFailed):
                run_pending_migrations(self.conn,expected_pending_sha256=plan['pending_sha256'])
        self.assertEqual(before,list(self.conn.iterdump()))
        self.assertEqual(len(plan_migrations(self.conn)['applied']),8)
        self.assertFalse(self.conn.in_transaction)
