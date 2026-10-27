"""Combined pending controller capabilities, offline on migrated file databases."""
from contextlib import closing
import sqlite3

from tests import test_reconnect_flow as reconnect_fixtures
from study_buddy.controller import start_quiz_session
from study_buddy.lessons import load_lessons
from study_buddy.maintenance_worker import run_maintenance_cycle
from study_buddy.migration_baseline import plan_baseline, record_verified_baseline
from study_buddy.migration_runner import run_pending_migrations
from study_buddy.migrations import plan_migrations
from study_buddy.session_flow import resume_summary
from study_buddy.session_start_requests import reserve_session_start, StartRequestRejected

LEARNER = reconnect_fixtures.LEARNER
OPTIONS = dict(request_id='integrated_start_01', lesson_version=2)


class ControllerIntegrationTests(reconnect_fixtures.ReconnectFlowTests):
    """Run all three reconnect scenarios through reservation + migration 009."""
    def setUp(self):
        super().setUp()
        # Replace the base fixture's fresh-schema DB with a real 008 -> 009 upgrade.
        self.path.unlink()
        with closing(self.connect()) as conn:
            root = reconnect_fixtures.fixtures.ROOT
            conn.executescript((root / 'db/baselines/008_schema.sql').read_text())
            load_lessons(conn)
            conn.execute('INSERT INTO learners VALUES (?)', (LEARNER,))
            conn.commit()
            baseline = plan_baseline(conn, through_version=8)
            record_verified_baseline(conn, through_version=8,
                expected_schema_sha256=baseline['schema_sha256'],
                expected_manifest_sha256=baseline['manifest_sha256'],
                recorded_by='offline integration', reason='Disposable combined controller verification')
            plan = plan_migrations(conn)
            run_pending_migrations(conn, expected_pending_sha256=plan['pending_sha256'])
            self.assertEqual(len(plan_migrations(conn)['applied']), 9)

    def forbidden(self, *args, **kwargs):
        self.fail('Replay dispatched agents or another workflow')

    def start(self):
        with closing(self.connect()) as conn:
            result = start_quiz_session(conn, LEARNER, 'lesson_cell_membranes',
                tutor=self.helper.tutor, assessment=self.helper.assessment,
                reviewer=reconnect_fixtures.passing_review, **OPTIONS)
        self.sid = result['session_id']
        return result['quiz']

    def reconnect(self, expected, quiz=None, scores=None):
        with closing(self.connect()) as conn:
            replay = start_quiz_session(conn, LEARNER, 'lesson_cell_membranes',
                tutor=self.forbidden, assessment=self.forbidden, reviewer=self.forbidden, **OPTIONS)
            self.assertFalse(replay['reserved'])
            self.assertEqual((replay['session_id'], replay['status']), (self.sid, expected))
            self.assertIsNone(replay['quiz'])
            # Even after the start deadline, quality/learning/completed states own progress.
            conn.execute("UPDATE session_start_requests SET created_at='2000-01-01T00:00:00.000Z'")
            conn.commit()
        self.assertEqual(run_maintenance_cycle(self.path)['session_starts_expired'], 0)
        super().reconnect(expected, quiz=quiz, scores=scores)

    def submit(self, quiz, wrong):
        original = super().submit(quiz, wrong)
        repeated = super().submit(quiz, wrong)
        self.assertEqual(original['attempt_id'], repeated['attempt_id'])
        self.assertEqual(original['graded'], repeated['graded'])
        return original

    def test_resume_after_save_failure_uses_existing_reserved_session(self):
        quiz = self.start()
        result = self.submit(quiz, set())
        self.route(result)
        with closing(self.connect()) as conn:
            conn.execute("CREATE TRIGGER fail_summary BEFORE INSERT ON session_summaries BEGIN SELECT RAISE(ABORT,'fixture'); END")
            conn.commit()
            failed = resume_summary(conn, LEARNER, self.sid)
        self.assertEqual(failed['next_action'], 'resume_summary')
        self.reconnect('pending_save')
        calls = (len(self.helper.tutor_calls), len(self.helper.assessment_calls), len(self.helper.feedback_calls))
        with closing(self.connect()) as conn:
            conn.execute('DROP TRIGGER fail_summary')
            conn.commit()
            done = resume_summary(conn, LEARNER, self.sid)
            self.assertEqual(done['status'], 'completed')
            self.assertEqual(resume_summary(conn, LEARNER, self.sid), done)
        self.reconnect('completed', scores=[10])
        self.assertEqual(calls, (len(self.helper.tutor_calls), len(self.helper.assessment_calls), len(self.helper.feedback_calls)))

    def test_abandoned_start_expires_and_reconnect_reports_failure_without_dispatch(self):
        with closing(self.connect()) as conn:
            claim = reserve_session_start(conn, LEARNER, OPTIONS['request_id'], 'lesson_cell_membranes', 2)
            self.sid = claim['session_id']
            conn.execute("UPDATE session_start_requests SET created_at='2000-01-01T00:00:00.000Z'")
            conn.commit()
        self.assertEqual(run_maintenance_cycle(self.path)['session_starts_expired'], 1)
        self.reconnect('failed')
        self.assertEqual(self.helper.tutor_calls, [])
        with closing(self.connect()) as conn:
            with self.assertRaises(StartRequestRejected):
                reserve_session_start(conn, LEARNER, OPTIONS['request_id'], 'lesson_cell_membranes', 3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM study_sessions').fetchone()[0], 1)
