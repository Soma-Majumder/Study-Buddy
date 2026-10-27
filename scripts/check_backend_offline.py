"""Run selected existing backend scenarios with fixture agents; no paid calls."""
from contextlib import closing
import os
import sqlite3
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# This command never opts into live calls, even if the parent shell did.
os.environ['STUDY_BUDDY_ALLOW_PAID_CLAUDE'] = 'false'

SCENARIOS = (
    ('Pass', '8/10 → feedback → saved summary; reconnect verified', (
        'tests.test_reconnect_flow.ReconnectFlowTests.test_pass_then_reconnect_at_quiz_grading_pending_and_completed',)),
    ('One retry', '6/10 → reteach → 10-question retry → summary; no second retry', (
        'tests.test_session_flow.SessionFlowTests.test_fail_reteach_one_retry_pass_then_summary',
        'tests.test_session_flow.SessionFlowTests.test_failing_the_retry_still_summarizes_and_never_offers_a_second_retry',)),
    ('Blocked quiz', 'uncertain review → no published quiz or submitted attempt', (
        'tests.test_session_flow.SessionFlowTests.test_unpublished_quiz_stops_before_any_answers',)),
)


def saved_summary_lines(test):
    """Read controller-owned records before the test removes its isolated DB."""
    from study_buddy.controller import get_saved_summary

    def read(conn):
        session_id, learner_id, status = conn.execute(
            'SELECT session_id, learner_id, status FROM study_sessions').fetchone()
        if status != 'completed':
            count = conn.execute('SELECT COUNT(*) FROM session_summaries').fetchone()[0]
            if count:
                raise AssertionError('Unfinished session unexpectedly has a saved summary.')
            return [f'Session {status}; no final summary saved.']
        summary = get_saved_summary(conn, learner_id, session_id)['summary']
        attempts = summary['attempts']
        first = attempts[0]
        retry = next((a for a in attempts if a['round'] == 'retry'), None)
        scores = f"First score: {first['score']}/{first['out_of']}. "
        scores += f"Retry score: {retry['score']}/{retry['out_of']}." if retry else 'No retry taken.'
        concepts = ', '.join(c['name'] for c in summary['concepts_to_revisit']) or 'none flagged in the final attempt'
        return [scores, f"Learning retries used: {summary['learning_retry_count']} of 1. Still to practice: {concepts}.",
                f"Session {status}; final summary saved and read back.", summary['message']]

    if hasattr(test, 'path'):
        with closing(sqlite3.connect(test.path.as_uri() + '?mode=ro', uri=True)) as conn:
            return read(conn)
    return read(test.conn)


def collect_saved_summaries(suite, reports):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            collect_saved_summaries(test, reports)
            continue
        # Keep existing tests intact; observe saved records just before cleanup.
        original_teardown = test.tearDown
        def teardown(test=test, original=original_teardown):
            try:
                reports.append(saved_summary_lines(test))
            finally:
                original()
        test.tearDown = teardown


def main():
    print('Study Buddy · Offline backend checks', flush=True)
    print('Fixture agents and isolated SQLite databases. Live calls disabled.\n', flush=True)
    failures = 0
    total = 0
    for name, description, tests in SCENARIOS:
        suite = unittest.defaultTestLoader.loadTestsFromNames(tests)
        summaries = []
        collect_saved_summaries(suite, summaries)
        result = unittest.TestResult()
        suite.run(result)
        total += result.testsRun
        ok = result.wasSuccessful() and not result.skipped and result.testsRun == len(tests)
        print(f"{'PASS' if ok else 'FAIL'} | {name}: {description}", flush=True)
        if ok:
            for lines in summaries:
                for line in lines:
                    print('  ' + line, flush=True)
        if not ok:
            failures += 1
            for test, traceback in result.failures + result.errors:
                print(f'\n{test}\n{traceback}', file=sys.stderr)
            for test, reason in result.skipped:
                print(f'SKIPPED (not verified): {test}: {reason}', file=sys.stderr)
    print(f'\n{3 - failures}/3 scenarios passed ({total} existing tests).', flush=True)
    print('Verifies backend coordination and persistence, not live AI teaching or quiz quality.', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
