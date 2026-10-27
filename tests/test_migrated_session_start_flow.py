"""Exercise migrated, file-backed session starts with offline agent fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from study_buddy.answer_keys import get_answer_key
from study_buddy.lessons import load_lessons
from study_buddy.migration_baseline import plan_baseline, record_verified_baseline
from study_buddy.migration_runner import run_pending_migrations
from study_buddy.migrations import plan_migrations
from study_buddy.session_flow import run_study_session
from tests import test_session_flow as fixtures
from tests.quality_fixtures import passing_review

ROOT = Path(__file__).resolve().parent.parent
LEARNER = fixtures.LEARNER
OPTIONS = dict(request_id='migrated_full_flow_01', lesson_version=2)


def exercise_migrated_flow(case, path):
    """Return safe evidence; never invoke a provider or touch the application DB."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript((ROOT / 'db/baselines/008_schema.sql').read_text())
        load_lessons(conn)
        conn.execute('INSERT INTO learners VALUES (?)', (LEARNER,))
        conn.commit()
        baseline = plan_baseline(conn, through_version=8)
        record_verified_baseline(conn, through_version=8,
            expected_schema_sha256=baseline['schema_sha256'],
            expected_manifest_sha256=baseline['manifest_sha256'],
            recorded_by='offline integration test', reason='Disposable migration rehearsal')
        plan = plan_migrations(conn)
        case.assertEqual([m['version'] for m in plan['pending']], [9])
        run_pending_migrations(conn, expected_pending_sha256=plan['pending_sha256'])
        case.assertEqual(len(plan_migrations(conn)['applied']), 9)

    helper = fixtures.SessionFlowTests()
    helper.setUp()
    entered, release = threading.Event(), threading.Event()
    counts = dict(tutor=0, assessment=0, reviewer=0, feedback=0, answer_prompts=0)
    lock = threading.Lock()

    def bump(name):
        with lock:
            counts[name] += 1

    def forbidden(*args, **kwargs):
        case.fail('A duplicate request dispatched learning work')

    def replay():
        with closing(sqlite3.connect(path, timeout=2)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            before = conn.total_changes
            result = run_study_session(conn, LEARNER, 'lesson_cell_membranes', forbidden,
                tutor=forbidden, assessment=forbidden, reviewer=forbidden, feedback=forbidden, **OPTIONS)
            case.assertEqual(conn.total_changes, before)
            return result

    def winner():
        with closing(sqlite3.connect(path, timeout=5)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')

            def tutor(context):
                bump('tutor')
                case.assertFalse(conn.in_transaction)
                entered.set()
                if not release.wait(10):
                    raise AssertionError('Tutor was not released')
                return helper.tutor(context)

            def assessment(*args, **kwargs):
                bump('assessment')
                return helper.assessment(*args, **kwargs)

            def reviewer(*args, **kwargs):
                bump('reviewer')
                return passing_review(*args, **kwargs)

            def feedback(*args, **kwargs):
                bump('feedback')
                return helper.feedback(*args, **kwargs)

            def answer(quiz):
                bump('answer_prompts')
                # Only the test's simulated learner reads the private key.
                return {k['question_id']: k['correct_option'] for k in get_answer_key(conn, quiz['quiz_id'])}

            return run_study_session(conn, LEARNER, 'lesson_cell_membranes', answer,
                tutor=tutor, assessment=assessment, reviewer=reviewer, feedback=feedback, **OPTIONS)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(winner)
            try:
                case.assertTrue(entered.wait(5), 'Winner never reached Tutor')
                during = pool.submit(replay).result(timeout=3)
                case.assertFalse(future.done(), 'Winner should still be waiting inside Tutor')
                case.assertEqual(during['status'], 'teaching')
                case.assertFalse(during['reserved'])
            finally:
                release.set()
            done = future.result(timeout=15)
        case.assertEqual((done['status'], done['scores']), ('completed', [10]))
        case.assertIsNotNone(done['summary'])
        after = replay()
        case.assertEqual(after['status'], 'completed')
        case.assertFalse(after['reserved'])
        case.assertEqual(after['next_action'], 'read_session_status')
        case.assertEqual({r['session_id'] for r in (during, done, after)}, {done['session_id']})
        case.assertEqual(counts, dict(tutor=1, assessment=1, reviewer=1, feedback=1, answer_prompts=1))
        with closing(sqlite3.connect(path)) as conn:
            tables = ('session_start_requests', 'study_sessions', 'handoff_repairs',
                      'explanations', 'quizzes', 'attempts', 'results', 'session_summaries')
            rows = {name: conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in tables}
            expected_rows = dict.fromkeys(tables, 1)
            # Outgoing lesson and return feedback each own a repair record.
            expected_rows['handoff_repairs'] = 2
            case.assertEqual(rows, expected_rows)
            case.assertEqual(conn.execute('SELECT SUM(repair_count) FROM handoff_repairs').fetchone()[0], 0)
            case.assertEqual(conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)
            case.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        return dict(result='pass', agents='offline fixtures', migration=9, requests=3,
                    session_id=done['session_id'], duplicate_statuses=[during['status'], after['status']],
                    calls=counts, rows=rows, score=10, paid_calls=0)
    finally:
        helper.doCleanups()


class MigratedSessionStartFlowTests(unittest.TestCase):
    def test_migration_009_full_flow_dispatches_tutor_once_across_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            exercise_migrated_flow(self, Path(directory) / 'rehearsal.db')
