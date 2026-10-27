"""Conservative baseline preview: actual schema/evidence, never inferred history."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from study_buddy.migration_baseline import plan_baseline
from study_buddy.migrations import initialize_migration_tracking, record_applied_migration, MigrationTrackingRejected

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT/'db/baselines/008_schema.sql').read_text()


class MigrationBaselineTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:'); self.addCleanup(self.conn.close)
        self.conn.executescript(SCHEMA)

    def preview(self): return plan_baseline(self.conn, through_version=8)

    def test_full_schema_is_eligible_without_writing_tracking_or_touching_data(self):
        self.conn.execute("INSERT INTO learners VALUES ('learner_keep')"); self.conn.commit()
        before = list(self.conn.iterdump())
        result = self.preview()
        self.assertEqual((result['status'], result['records_written']), ('baseline_ready',0))
        self.assertEqual([r['version'] for r in result['would_record']], list(range(1,9)))
        self.assertRegex(result['schema_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(before,list(self.conn.iterdump()))
        self.assertNotIn('learner_keep',json.dumps(result))

    def test_empty_tracking_is_allowed_but_any_recorded_history_is_rejected(self):
        initialize_migration_tracking(self.conn)
        self.assertEqual(self.preview()['status'],'baseline_ready')
        path=ROOT/'db/migrations/001_feedback_operations.sql'
        record_applied_migration(self.conn,path.name,path.read_bytes())
        with self.assertRaises(MigrationTrackingRejected):self.preview()

    def test_explicit_current_version_is_required(self):
        for version in (0,1,7,10,True,'8'):
            with self.assertRaises(MigrationTrackingRejected):plan_baseline(self.conn,through_version=version)

    def test_missing_table_or_partial_schema_is_not_silently_baselined(self):
        self.conn.execute('DROP TABLE quiz_quality_inputs')
        with self.assertRaises(MigrationTrackingRejected):self.preview()

    def test_same_table_names_without_constraints_are_not_enough(self):
        self.conn.execute('DROP TABLE quiz_quality_inputs')
        self.conn.execute('CREATE TABLE quiz_quality_inputs (operation_id TEXT, handoff_data TEXT)')
        with self.assertRaises(MigrationTrackingRejected):self.preview()

    def test_missing_partial_index_is_rejected(self):
        self.conn.execute('DROP INDEX one_learning_retry_route')
        with self.assertRaises(MigrationTrackingRejected):self.preview()

    def test_extra_objects_are_rejected_for_explicit_manual_review(self):
        self.conn.execute('CREATE TABLE custom_extension (value TEXT)')
        with self.assertRaises(MigrationTrackingRejected):self.preview()

    def test_changed_check_or_foreign_key_is_detected(self):
        for schema in (SCHEMA.replace('CHECK (score BETWEEN 0 AND 10)','CHECK (score BETWEEN 0 AND 100)'),
                       SCHEMA.replace('REFERENCES questions (question_id)','REFERENCES questions (position)')):
            with closing(sqlite3.connect(':memory:')) as conn:
                conn.executescript(schema)
                with self.assertRaises(MigrationTrackingRejected):plan_baseline(conn,through_version=8)

    def test_equivalent_unquoted_case_spacing_and_comments_are_accepted(self):
        schema=SCHEMA.replace('CREATE TABLE learners (','create   table learners\n(').replace('learner_id TEXT PRIMARY KEY\n','learner_id text /* equivalent */ primary key\n')
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.executescript(schema)
            self.assertEqual(plan_baseline(conn,through_version=8)['schema_sha256'],self.preview()['schema_sha256'])

    def test_case_or_comment_like_text_inside_string_literals_is_not_normalized(self):
        schema=SCHEMA.replace("DEFAULT 'private_quiz_v1'", "DEFAULT 'PRIVATE_QUIZ_V1'")
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.executescript(schema)
            with self.assertRaises(MigrationTrackingRejected):plan_baseline(conn,through_version=8)

    def test_foreign_key_violation_blocks_baseline_even_when_enforcement_was_disabled(self):
        self.conn.execute('PRAGMA foreign_keys=OFF')
        self.conn.execute("INSERT INTO explanations VALUES ('expl_bad','session_missing','first','private content')"); self.conn.commit()
        with self.assertRaises(MigrationTrackingRejected):self.preview()
        self.assertEqual(self.conn.execute('PRAGMA foreign_keys').fetchone()[0],0)

    def test_invalid_existing_check_values_block_baseline(self):
        self.conn.execute('PRAGMA ignore_check_constraints=ON')
        self.conn.execute("INSERT INTO learners VALUES ('learner_keep')")
        # Use a table with no FK dependency for a pure CHECK violation.
        self.conn.execute("INSERT INTO lessons (lesson_id,version,topic,source_text,source_url,license,attribution,approved) VALUES ('bad',0,'topic','text','url','license','credit',0)")
        self.conn.commit(); self.conn.execute('PRAGMA ignore_check_constraints=OFF')
        with self.assertRaises(MigrationTrackingRejected):self.preview()

    def test_read_only_preview_does_not_commit_callers_pending_writes(self):
        self.conn.execute("INSERT INTO learners VALUES ('learner_pending')")
        self.preview(); self.assertTrue(self.conn.in_transaction)
        self.conn.rollback(); self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM learners').fetchone()[0],0)

    def test_real_command_is_read_only_and_missing_paths_are_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'study.db'
            with closing(sqlite3.connect(path)) as conn:conn.executescript(SCHEMA)
            before=path.read_bytes()
            command=[sys.executable,'-B',str(ROOT/'scripts/baseline_status.py'),str(path),'--through-version','8']
            result=subprocess.run(command,text=True,capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'],'baseline_ready')
            self.assertEqual(before,path.read_bytes())
            missing=Path(directory)/'missing.db';command[3]=str(missing)
            result=subprocess.run(command,text=True,capture_output=True,timeout=5)
            self.assertNotEqual(result.returncode,0);self.assertFalse(missing.exists())

    def test_malformed_empty_tracking_table_cannot_be_adopted(self):
        self.conn.execute('CREATE TABLE schema_migrations (version INTEGER, filename TEXT, sha256 TEXT, applied_at TEXT)')
        with self.assertRaises(MigrationTrackingRejected):self.preview()
