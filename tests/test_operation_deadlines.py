"""Timing is durable and atomic with claims, and never refreshed by replay/repair."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from tests import test_feedback as fixtures
from study_buddy.controller import decide_next_step, dispatch_reteaching
from study_buddy.migration_baseline import plan_baseline, record_verified_baseline
from study_buddy.migrations import plan_migrations
from study_buddy.migration_runner import run_pending_migrations, MigrationExecutionFailed

ROOT = Path(__file__).resolve().parent.parent
LEARNER = fixtures.LEARNER


def crash(*args, **kwargs):
    raise KeyboardInterrupt('Simulated process loss')


def forbidden(*args, **kwargs):
    raise AssertionError('A replay dispatched a provider')


class OperationDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.h = fixtures.FeedbackTests(); self.h.setUp(); self.addCleanup(self.h.doCleanups)
        self.conn = self.h.conn

    def test_feedback_claim_and_single_repair_share_one_window(self):
        seen = []
        def feedback(context, **kwargs):
            self.assertFalse(self.conn.in_transaction)
            seen.append(self.conn.execute('SELECT started_at,deadline_at FROM feedback_operation_deadlines').fetchone())
            out = self.h.feedback(context, **kwargs)
            if len(seen) == 1:
                out['summary'] = ''
            return out
        with patch('study_buddy.operation_deadlines.time.time', return_value=1000):
            self.assertEqual(self.h.explain(feedback)['operation_status'], 'ready')
        self.assertEqual(seen, [(1000, 1180), (1000, 1180)])
        self.h.explain(forbidden)
        self.assertEqual(self.conn.execute('SELECT started_at,deadline_at FROM feedback_operation_deadlines').fetchone(), (1000, 1180))
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs WHERE handoff_id IN (SELECT handoff_id FROM feedback_operations)').fetchone()[0], 1)

    def test_feedback_crash_timing_survives_reopen_without_regrant(self):
        with patch('study_buddy.operation_deadlines.time.time', return_value=2000):
            with self.assertRaises(KeyboardInterrupt):
                self.h.explain(crash)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deadline.db'
            with closing(sqlite3.connect(path)) as disk:
                self.conn.backup(disk)
            with closing(sqlite3.connect(path)) as reopened:
                from study_buddy.feedback import explain_results
                out = explain_results(reopened, LEARNER, self.h.attempt_id, assessment=forbidden)
                self.assertEqual(out['operation_status'], 'failed')
                self.assertEqual(reopened.execute('SELECT started_at,deadline_at FROM feedback_operation_deadlines').fetchone(), (2000, 2180))
                self.assertEqual(reopened.execute('SELECT score FROM results').fetchone()[0], 6)

    def test_failed_feedback_timestamp_save_rolls_back_dispatch_claim(self):
        self.conn.execute("CREATE TRIGGER reject_timing BEFORE INSERT ON feedback_operation_deadlines BEGIN SELECT RAISE(ABORT,'fixture'); END")
        self.conn.commit()
        before = list(self.conn.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            self.h.explain(forbidden)
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_reteaching_deadline_starts_at_retry_reservation_not_dispatch(self):
        self.h.explain()
        with patch('study_buddy.operation_deadlines.time.time', return_value=3000):
            decide_next_step(self.conn, LEARNER, self.h.attempt_id)
        expected = (3000, 3120)
        self.assertEqual(self.conn.execute('SELECT started_at,deadline_at FROM reteaching_operation_deadlines').fetchone(), expected)
        decide_next_step(self.conn, LEARNER, self.h.attempt_id)
        with patch('study_buddy.operation_deadlines.time.time', return_value=3000):
            with self.assertRaises(KeyboardInterrupt):
                dispatch_reteaching(self.conn, LEARNER, self.h.attempt_id, tutor=crash)
        replay = dispatch_reteaching(self.conn, LEARNER, self.h.attempt_id, tutor=forbidden)
        self.assertEqual(replay['operation_status'], 'failed')
        self.assertEqual(self.conn.execute('SELECT started_at,deadline_at FROM reteaching_operation_deadlines').fetchone(), expected)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 1)

    def test_failed_reteaching_timestamp_save_does_not_consume_learning_retry(self):
        self.h.explain()
        self.conn.execute("CREATE TRIGGER reject_timing BEFORE INSERT ON reteaching_operation_deadlines BEGIN SELECT RAISE(ABORT,'fixture'); END")
        self.conn.commit()
        before = list(self.conn.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            decide_next_step(self.conn, LEARNER, self.h.attempt_id)
        self.assertEqual(before, list(self.conn.iterdump()))
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)

    def test_database_rejects_changed_windows_and_orphan_records(self):
        self.h.explain()
        for statement in (
            'UPDATE feedback_operation_deadlines SET deadline_at=started_at',
            "UPDATE feedback_operation_deadlines SET started_at=-1,deadline_at=179",
            "INSERT INTO feedback_operation_deadlines VALUES ('attempt_missing',100,280)",
            'INSERT INTO feedback_operation_deadlines VALUES (NULL,100,280)',
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.IntegrityError):
                    with self.conn:
                        self.conn.execute(statement)

    def test_summary_route_does_not_reserve_reteaching_timing(self):
        from tests import test_routing as routing_fixtures
        h = routing_fixtures.RoutingTests(); h.setUp(); self.addCleanup(h.doCleanups)
        result = h.prepare(missed=(1, 2))
        decide_next_step(h.conn, LEARNER, result['attempt_id'])
        self.assertEqual(h.conn.execute('SELECT COUNT(*) FROM reteaching_operation_deadlines').fetchone()[0], 0)


class OperationDeadlineMigrationTests(unittest.TestCase):
    def baseline(self, conn, version):
        plan = plan_baseline(conn, through_version=version)
        record_verified_baseline(conn, through_version=version,
            expected_schema_sha256=plan['schema_sha256'], expected_manifest_sha256=plan['manifest_sha256'],
            recorded_by='offline timing test', reason='Disposable baseline')

    def legacy(self, kind):
        h = fixtures.FeedbackTests(); h.setUp(); self.addCleanup(h.doCleanups)
        if kind == 'feedback':
            with self.assertRaises(KeyboardInterrupt): h.explain(crash)
        else:
            h.explain()
            decide_next_step(h.conn, LEARNER, h.attempt_id)
            if kind == 'dispatched':
                with self.assertRaises(KeyboardInterrupt):
                    dispatch_reteaching(h.conn, LEARNER, h.attempt_id, tutor=crash)
        h.conn.execute('DROP TABLE feedback_operation_deadlines')
        h.conn.execute('DROP TABLE reteaching_operation_deadlines')
        h.conn.commit()
        self.baseline(h.conn, 9)
        return h

    def test_legacy_active_work_receives_one_adoption_window_with_existing_evidence_preserved(self):
        for kind in ('feedback', 'reserved', 'dispatched'):
            with self.subTest(kind=kind):
                h = self.legacy(kind)
                tables = ('results', 'responses', 'weak_concepts', 'study_sessions', 'feedback_operations', 'session_routes', 'handoff_repairs')
                before = {t: h.conn.execute(f'SELECT * FROM {t}').fetchall() for t in tables}
                plan = plan_migrations(h.conn)
                self.assertEqual([m['version'] for m in plan['pending']], [10])
                earliest = int(time.time())
                run_pending_migrations(h.conn, expected_pending_sha256=plan['pending_sha256'])
                latest = int(time.time())
                table = 'feedback_operation_deadlines' if kind == 'feedback' else 'reteaching_operation_deadlines'
                started, deadline = h.conn.execute(f'SELECT started_at,deadline_at FROM {table}').fetchone()
                self.assertTrue(earliest <= started <= latest)
                self.assertEqual(deadline-started, 180 if kind == 'feedback' else 120)
                self.assertEqual(before, {t: h.conn.execute(f'SELECT * FROM {t}').fetchall() for t in tables})
                saved = list(h.conn.iterdump())
                plan = plan_migrations(h.conn)
                run_pending_migrations(h.conn, expected_pending_sha256=plan['pending_sha256'])
                self.assertEqual(saved, list(h.conn.iterdump()))
                with closing(sqlite3.connect(':memory:')) as fresh:
                    fresh.executescript((ROOT/'db/schema.sql').read_text())
                    for name in ('feedback_operation_deadlines', 'reteaching_operation_deadlines'):
                        query = 'SELECT sql FROM sqlite_master WHERE name=?'
                        self.assertEqual(h.conn.execute(query,(name,)).fetchone(), fresh.execute(query,(name,)).fetchone())

    def test_history_failure_rolls_back_new_tables_and_backfilled_deadlines(self):
        h = self.legacy('feedback')
        before = list(h.conn.iterdump())
        plan = plan_migrations(h.conn)
        with patch('study_buddy.migration_runner.record_applied_migration', side_effect=sqlite3.OperationalError('fixture')):
            with self.assertRaises(MigrationExecutionFailed):
                run_pending_migrations(h.conn, expected_pending_sha256=plan['pending_sha256'])
        self.assertEqual(before, list(h.conn.iterdump()))

    def test_fresh_schema_can_baseline_010_but_cannot_claim_009(self):
        with closing(sqlite3.connect(':memory:')) as fresh:
            fresh.executescript((ROOT/'db/schema.sql').read_text())
            with self.assertRaises(ValueError): plan_baseline(fresh, through_version=9)
            self.baseline(fresh,10)
            self.assertEqual(plan_migrations(fresh)['status'], 'up_to_date')
