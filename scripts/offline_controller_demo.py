"""Offline controller scenarios: fixture agents, temporary SQLite, no credentials needed."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.offline_controller_fixtures import OfflineAgents, forbidden
from study_buddy.answer_keys import get_answer_key
from study_buddy.controller import get_session_status, get_saved_summary, submit_answers, SubmissionRejected
from study_buddy.lessons import load_lessons
from study_buddy.maintenance_worker import run_maintenance_cycle
from study_buddy.session_flow import run_study_session, resume_summary
from study_buddy.session_start_requests import reserve_session_start

ROOT = Path(__file__).resolve().parent.parent
LEARNER = 'learner_a1b2c3d4e5f6'
LESSON = 'lesson_cell_membranes'
OPTIONS = dict(request_id='offline_demo_start_01', lesson_version=2)
SCENARIOS = {
    'pass': 'Pass: 8/10, feedback, saved summary',
    'practice': 'Practice: 6/10, reteach, one retry, summary',
    'recovery': 'Recovery: failed summary save, then resume',
    'crash': 'Crash: abandoned start expires without a Tutor call',
    'duplicates': 'Duplicates: repeated starts and answers reuse saved records',
}


def require(condition, message):
    if not condition:
        raise RuntimeError('Scenario verification failed: ' + message)


def replay(conn):
    return run_study_session(conn, LEARNER, LESSON, forbidden, tutor=forbidden,
        assessment=forbidden, reviewer=forbidden, feedback=forbidden,
        revision_assessment=forbidden, **OPTIONS)


def run_scenario(name, emit=print):
    if name not in SCENARIOS:
        raise ValueError('Unknown offline scenario.')
    emit('\n' + SCENARIOS[name])
    emit('Flow: Start → Tutor → Assessment → Review → Grade → Feedback → Summary')
    agents = OfflineAgents(emit)
    # No database path option: this tool only opens the database it creates.
    with tempfile.TemporaryDirectory(prefix='study-buddy-offline-') as directory:
        path = Path(directory) / 'demo.db'
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript((ROOT / 'db/schema.sql').read_text())
            load_lessons(conn)
            conn.execute('INSERT INTO learners VALUES (?)', (LEARNER,))
            conn.commit()
            captured = []
            guidance = []

            def record_guidance(connection, session_id, label):
                snapshot = get_session_status(connection, LEARNER, session_id)
                guidance.append(dict(label=label, status=snapshot['status'], next_action=snapshot['next_action']))
                return snapshot

            if name == 'crash':
                claim = reserve_session_start(conn, LEARNER, OPTIONS['request_id'], LESSON, 2)
                sid = claim['session_id']
                emit('Controller: reserve Start; simulate process loss before Tutor')
                conn.execute("UPDATE session_start_requests SET created_at='2000-01-01T00:00:00.000Z'")
                conn.commit()
                counts = run_maintenance_cycle(path)
                require(counts['session_starts_expired'] == 1, 'worker must expire one abandoned start')
                emit('Maintenance: deadline passed → mark session failed; retain request claim')
                repeated = replay(conn)
                require(repeated['session_id'] == sid and repeated['status'] == 'failed' and not repeated['reserved'],
                        'expired replay must reuse failed session')
                result = dict(session_id=sid, status='failed', scores=[], steps=[])
            else:
                if name == 'recovery':
                    conn.execute("CREATE TRIGGER demo_fail_summary BEFORE INSERT ON session_summaries BEGIN SELECT RAISE(ABORT,'demo save failure'); END")
                    conn.commit()
                wrong_rounds = iter(({3, 4, 6, 7}, set()) if name == 'practice' else ({1, 2},))

                def answer(quiz):
                    current_sid = conn.execute('SELECT session_id FROM quizzes WHERE quiz_id=?', (quiz['quiz_id'],)).fetchone()[0]
                    record_guidance(conn, current_sid, 'Quiz ready')
                    if name == 'duplicates':
                        duplicate = replay(conn)
                        require(not duplicate['reserved'] and duplicate['status'] == 'awaiting_answers',
                                'duplicate Start must not dispatch again')
                        emit('Duplicate Start: return existing session; no extra agent calls')
                    wrong = next(wrong_rounds)
                    # Simulated learner uses private fixture answers internally.
                    # The terminal never displays keys or answers.
                    keys = get_answer_key(conn, quiz['quiz_id'])
                    answers = {k['question_id']: (('b' if k['correct_option'] == 'a' else 'a')
                               if i in wrong else k['correct_option']) for i, k in enumerate(keys, 1)}
                    captured.append((quiz, answers))
                    emit(f'Simulated learner: submit {len(answers)} answers ({len(wrong)} deliberately wrong)')
                    return answers

                result = run_study_session(conn, LEARNER, LESSON, answer, **agents.runners(), **OPTIONS)
                sid = result['session_id']
                emit('Saved controller trace:')
                for step in result['steps']:
                    score = f"; score {step['score']}/10" if 'score' in step else ''
                    emit(f"Controller: {step['step']} [{step.get('round', 'first')}] → {step['status']}{score}")
                if name == 'recovery':
                    require(result['status'] == 'pending_save' and result.get('next_action') == 'resume_summary',
                            'save failure must retain pending_save')
                    require(conn.execute('SELECT COUNT(*) FROM session_summaries').fetchone()[0] == 0,
                            'failed save must not claim a summary')
                    record_guidance(conn, sid, 'Summary save failed')
                    calls_before = dict(agents.calls)
                    conn.execute('DROP TRIGGER demo_fail_summary')
                    conn.commit()
                    result = resume_summary(conn, LEARNER, sid)
                    require(agents.calls == calls_before, 'recovery must not call agents')
                    emit('Recovery: resume same session → summary saved; no agents restarted')
                expected_scores = [6, 10] if name == 'practice' else [8]
                require(result['status'] == 'completed' and result['scores'] == expected_scores,
                        'completion and scores must match scenario')
                if name == 'duplicates':
                    quiz, answers = captured[0]
                    before = conn.execute('SELECT attempt_id FROM attempts').fetchone()[0]
                    saved = submit_answers(conn, LEARNER, quiz['quiz_id'], answers)
                    require(saved['attempt_id'] == before, 'identical answers must reuse original attempt')
                    changed = dict(answers)
                    key = next(iter(changed))
                    changed[key] = 'b' if changed[key] == 'a' else 'a'
                    try:
                        submit_answers(conn, LEARNER, quiz['quiz_id'], changed)
                    except SubmissionRejected:
                        emit('Duplicate answers: reuse original attempt; changed answers rejected')
                    else:
                        raise RuntimeError('Changed duplicate answers were accepted')
                    repeated = replay(conn)
                    require(repeated['session_id'] == sid and repeated['status'] == 'completed',
                            'completed replay must keep existing session')
            rows = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                    for table in ('study_sessions', 'session_start_requests', 'quizzes', 'attempts', 'results', 'session_summaries')}
            require(rows['study_sessions'] == rows['session_start_requests'] == 1, 'one durable session and claim')
            rounds = 2 if name == 'practice' else 0 if name == 'crash' else 1
            require(all(rows[t] == rounds for t in ('quizzes', 'attempts', 'results')), 'one quiz/attempt/result per round')
            require(rows['session_summaries'] == int(name != 'crash'), 'summary availability')
            require(all(count == rounds for count in agents.calls.values()), 'expected fixture agent calls only')
        # Reopen read-only, as a learner reconnect would.
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as reader:
            status = record_guidance(reader, sid, 'Final reconnect')
            require(status['status'] == result['status'], 'saved state matches returned state')
            if name != 'crash':
                summary = get_saved_summary(reader, LEARNER, sid)['summary']
                require([a['score'] for a in summary['attempts']] == result['scores'], 'durable summary scores')
            emit(f"Reconnect: saved status = {status['status']}; summary available = {status['summary_available']}")
        report = dict(scenario=name, status=result['status'], scores=result['scores'],
                      fixture_calls=agents.calls, rows=rows, paid_calls=0,
                      next_action=status['next_action'], guidance_history=guidance)
        emit('Verified: ' + json.dumps(report, sort_keys=True))
    emit('Disposable demo database removed.\n')
    return report


def main(argv=None, input_fn=input, emit=print):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=[*SCENARIOS, 'all'], help='Run once without the menu')
    args = parser.parse_args(argv)
    emit('Study Buddy — OFFLINE CONTROLLER DEMO')
    emit('Fixture agents and simulated answers. No Claude calls, API key, or application database. '
         'This demonstrates routing and persistence, not educational content quality.')
    if args.scenario:
        for name in SCENARIOS if args.scenario == 'all' else [args.scenario]:
            run_scenario(name, emit)
        return 0
    while True:
        emit('\nChoose a scenario:')
        for index, label in enumerate(SCENARIOS.values(), 1):
            emit(f'{index}. {label}')
        emit('a. Run all scenarios\nq. Quit')
        try:
            choice = input_fn('Your choice: ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            emit('\nDemo closed.')
            return 0
        if choice == 'q':
            emit('Demo closed.')
            return 0
        if choice == 'a':
            names = list(SCENARIOS)
        elif choice in {str(i) for i in range(1, len(SCENARIOS) + 1)}:
            names = [list(SCENARIOS)[int(choice) - 1]]
        else:
            emit('Choose 1–5, a, or q.')
            continue
        for name in names:
            run_scenario(name, emit)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\nDemo interrupted; temporary database cleaned up.')
        raise SystemExit(130)
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
        print(f'Offline demo failed: {error}', file=sys.stderr)
        raise SystemExit(1)
