"""Explicit baseline transaction, stale previews, audit, rollback and backup CLI."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from study_buddy.migration_baseline import plan_baseline, record_verified_baseline
from study_buddy.migrations import (initialize_migration_tracking, list_applied_migrations,
                                   plan_migrations, MigrationTrackingRejected)

ROOT=Path(__file__).resolve().parent.parent
SCHEMA=(ROOT/'db/baselines/008_schema.sql').read_text()


class BaselineRecordingTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.addCleanup(self.conn.close)
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO learners VALUES ('learner_preserve')");self.conn.commit()
        preview=plan_baseline(self.conn,through_version=8)
        self.options=dict(through_version=8,expected_schema_sha256=preview['schema_sha256'],
                          expected_manifest_sha256=preview['manifest_sha256'],recorded_by='test operator',reason='Adopt full-schema test database')

    def record(self,**changes):return record_verified_baseline(self.conn,**dict(self.options,**changes))

    def test_records_eight_versions_and_baseline_audit_without_changing_application_data(self):
        result=self.record()
        self.assertEqual((result['status'],result['records_written']),('baselined',8))
        self.assertEqual(len(list_applied_migrations(self.conn)),8)
        audit=self.conn.execute('SELECT through_version,schema_sha256,manifest_sha256,recorded_by,reason,recorded_at FROM schema_baselines').fetchone()
        self.assertEqual(audit[:5],(8,self.options['expected_schema_sha256'],self.options['expected_manifest_sha256'],self.options['recorded_by'],self.options['reason']))
        self.assertEqual(audit[5],result['recorded_at'])
        self.assertEqual(self.conn.execute('SELECT learner_id FROM learners').fetchall(),[('learner_preserve',)])
        self.assertEqual([row['version'] for row in plan_migrations(self.conn)['pending']],[9,10])
        self.assertFalse(self.conn.in_transaction)

    def test_repeat_is_rejected_without_overwriting_history_or_audit(self):
        self.record();before=list(self.conn.iterdump())
        with self.assertRaises(MigrationTrackingRejected):self.record(reason='overwrite')
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_schema_change_since_preview_rejected_without_creating_metadata(self):
        self.conn.execute('DROP INDEX one_learning_retry_route')
        before=list(self.conn.iterdump())
        with self.assertRaises(MigrationTrackingRejected):self.record()
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_wrong_fingerprints_and_invalid_operator_details_do_not_write(self):
        for changes in [dict(expected_schema_sha256='0'*64),dict(expected_manifest_sha256='0'*64),
                        dict(expected_schema_sha256='bad'),dict(recorded_by=' '),dict(reason=''),dict(through_version=7)]:
            with self.subTest(changes=changes):
                before=list(self.conn.iterdump())
                with self.assertRaises(MigrationTrackingRejected):self.record(**changes)
                self.assertEqual(before,list(self.conn.iterdump()))
                self.assertFalse(self.conn.in_transaction)

    def test_failure_mid_history_rolls_back_created_tables_and_all_records(self):
        import study_buddy.migration_baseline as module
        original=module.record_applied_migration
        def fail_third(conn,name,data,**kwargs):
            if name.startswith('003_'):raise sqlite3.OperationalError('simulated save failure')
            return original(conn,name,data,**kwargs)
        before=list(self.conn.iterdump())
        with patch.object(module,'record_applied_migration',side_effect=fail_third):
            with self.assertRaises(sqlite3.Error):self.record()
        self.assertEqual(before,list(self.conn.iterdump()))
        self.assertFalse(self.conn.in_transaction)

    def test_audit_insert_failure_rolls_back_all_eight_records(self):
        # Use a separate existing empty tracking table to inject an audit failure
        # through the SQLite authorizer without changing application structure.
        initialize_migration_tracking(self.conn)
        before=list(self.conn.iterdump())
        self.conn.set_authorizer(lambda action,arg1,arg2,db,source:
            sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_INSERT and arg1=='schema_baselines' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.Error):self.record()
        finally:self.conn.set_authorizer(None)
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_caller_pending_work_is_not_committed_or_rolled_back(self):
        self.conn.execute("INSERT INTO learners VALUES ('learner_pending')")
        with self.assertRaises(MigrationTrackingRejected):self.record()
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM learners').fetchone()[0],1)

    def test_manifest_reread_change_rolls_back_before_recording(self):
        import study_buddy.migration_baseline as module
        original=module.discover_migrations
        calls=[]
        def changed(*args):
            files=original(*args);calls.append(1)
            if len(calls)==2:files[0]['sha256']='0'*64
            return files
        with patch.object(module,'discover_migrations',side_effect=changed):
            with self.assertRaises(MigrationTrackingRejected):self.record()
        self.assertEqual(self.conn.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchall(),[])

    def test_concurrent_attempts_create_one_audit_and_one_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.db'
            with closing(sqlite3.connect(path)) as conn:self.conn.backup(conn)
            def record():
                with closing(sqlite3.connect(path,timeout=5)) as conn:
                    try:return record_verified_baseline(conn,**self.options)['status']
                    except MigrationTrackingRejected:return 'already_recorded'
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:record(),range(2)))
            self.assertCountEqual(results,['baselined','already_recorded'])
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(len(list_applied_migrations(conn)),8)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_baselines').fetchone()[0],1)

    def command(self,path,backup,authorize=True):
        return [sys.executable,'-B',str(ROOT/'scripts/baseline_database.py'),str(path),
                '--through-version','8','--schema-sha256',self.options['expected_schema_sha256'],
                '--manifest-sha256',self.options['expected_manifest_sha256'],'--recorded-by','test operator',
                '--reason','Adopt full-schema test database','--backup',str(backup)]+(['--record-baseline'] if authorize else [])

    def test_real_command_creates_backup_and_records_only_with_explicit_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.db';backup=Path(directory)/'backup.db'
            with closing(sqlite3.connect(path)) as conn:self.conn.backup(conn)
            before=path.read_bytes()
            denied=subprocess.run(self.command(path,backup,False),text=True,capture_output=True,timeout=5)
            self.assertNotEqual(denied.returncode,0);self.assertFalse(backup.exists());self.assertEqual(before,path.read_bytes())
            result=subprocess.run(self.command(path,backup),text=True,capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['records_written'],8)
            self.assertEqual(backup.stat().st_mode&0o777,0o600)
            with closing(sqlite3.connect(backup)) as conn:
                self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone())
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM learners').fetchone()[0],1)

    def test_existing_backup_and_missing_database_are_never_overwritten_or_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.db';backup=Path(directory)/'backup.db'
            with closing(sqlite3.connect(path)) as conn:self.conn.backup(conn)
            backup.write_bytes(b'keep me');before=path.read_bytes()
            result=subprocess.run(self.command(path,backup),text=True,capture_output=True,timeout=5)
            self.assertNotEqual(result.returncode,0);self.assertEqual(backup.read_bytes(),b'keep me');self.assertEqual(path.read_bytes(),before)
            missing=Path(directory)/'missing.db'
            result=subprocess.run(self.command(missing,Path(directory)/'new-backup.db'),text=True,capture_output=True,timeout=5)
            self.assertNotEqual(result.returncode,0);self.assertFalse(missing.exists())
