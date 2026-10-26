"""Explicit paid end-to-end smoke run of one whole study session with the real agents.

Run from the repository root with ANTHROPIC_API_KEY in the process environment:
    python -B backend/evals/session_flow_live.py --live --output /absolute/path/report.json

Authorizes real Tutor, Assessment, Reviewer and feedback calls (about 10-15 short calls,
bounded by the controller's per-round budgets). Uses an isolated in-memory database and a
simulated learner: misses three questions in round one, then answers the retry quiz correctly.
The report holds step outcomes, scores, call counts and timing only: no quiz keys, questions,
explanations or credentials.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from study_buddy.answer_keys import get_answer_key
from study_buddy.lessons import load_lessons
from study_buddy.reviewer import run_reviewer
from study_buddy.session_flow import run_study_session

LEARNER = 'learner_a1b2c3d4e5f6'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Authorize paid model calls')
    parser.add_argument('--output', required=True, help='Absolute path for the JSON report')
    args = parser.parse_args()
    if not args.live:
        parser.error('Refusing to make paid calls without --live.')
    if not os.environ.get('ANTHROPIC_API_KEY', '').strip():
        parser.error('ANTHROPIC_API_KEY must be set in the environment.')
    out = Path(args.output)
    if not out.is_absolute():
        parser.error('--output must be an absolute path.')

    conn = sqlite3.connect(':memory:')
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript((ROOT / 'db/schema.sql').read_text())
    load_lessons(conn)
    conn.execute('INSERT INTO learners VALUES (?)', (LEARNER,))
    conn.commit()

    rounds = {'n': 0}

    def answer(quiz):
        rounds['n'] += 1
        keys = get_answer_key(conn, quiz['quiz_id'])
        miss = {3, 5, 8} if rounds['n'] == 1 else set()
        return {k['question_id']: (('b' if k['correct_option'] == 'a' else 'a') if i in miss else k['correct_option'])
                for i, k in enumerate(keys, 1)}

    review_errors = []

    def traced_reviewer(context, **kwargs):
        try:
            return run_reviewer(context, **kwargs)
        except Exception as error:  # Record only fixed, non-sensitive diagnostics.
            review_errors.append(dict(round=context['controller_context']['round'], type=type(error).__name__,
                                      stage=getattr(error, 'stage', None), code=getattr(error, 'code', None),
                                      http_status=getattr(error, 'http_status', None), stop_reason=getattr(error, 'stop_reason', None)))
            raise

    started = time.time()
    result = run_study_session(conn, LEARNER, 'lesson_cell_membranes', answer, reviewer=traced_reviewer)
    elapsed = round(time.time() - started, 1)
    counts = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in
              ('quizzes', 'attempts', 'results', 'quiz_quality_calls', 'quiz_quality_publications')}
    kinds = dict(conn.execute('SELECT kind, COUNT(*) FROM quiz_quality_calls GROUP BY kind').fetchall())
    session = conn.execute('SELECT status, learning_retry_count, failure_reason FROM study_sessions').fetchone()
    reviews = []
    for data, decision, version in conn.execute('SELECT report_data, decision, candidate_version FROM quiz_quality_reports ORDER BY rowid'):
        rep = json.loads(data)
        tally = {}
        for q in rep['questions']:
            for c in ('grounding', 'exactly_one_answer', 'plausible_distractors', 'fair_wording', 'rationale_quality'):
                if q[c] != 'pass':
                    tally.setdefault(c, {}).setdefault(q[c], []).append(q['position'])
        codes = sorted({i['code'] for q in rep['questions'] for i in q['issues']} | {i['code'] for i in rep['quiz_review']['issues']})
        demands = {}
        for q in rep['questions']:
            demands[q['primary_demand']] = demands.get(q['primary_demand'], 0) + 1
        reviews.append(dict(candidate_version=version, decision=decision, non_pass_by_criterion=tally, issue_codes=codes,
                            quiz_level={k: rep['quiz_review'][k] for k in ('concept_coverage', 'target_diversity')}, demands=demands))
    report = dict(date=datetime.now(timezone.utc).isoformat(), model=os.environ.get('ANTHROPIC_MODEL', 'default'),
                  elapsed_seconds=elapsed, status=result['status'], error=result['error'], scores=result['scores'],
                  steps=result['steps'], session=dict(status=session[0], learning_retry_count=session[1], failure_reason=session[2]),
                  row_counts=counts, quality_calls_by_kind=kinds, foreign_key_violations=len(conn.execute('PRAGMA foreign_key_check').fetchall()),
                  summary_saved=result['summary'] is not None, reviews=reviews, review_errors=review_errors)
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('status', 'error', 'scores', 'elapsed_seconds', 'row_counts', 'quality_calls_by_kind')}, indent=2))
    print('reviews:', json.dumps(reviews))
    print('review_errors:', json.dumps(review_errors))
    print('steps:', ' > '.join(f"{s['step']}:{s['round']}:{s['status']}" for s in result['steps']))


if __name__ == '__main__':
    main()
