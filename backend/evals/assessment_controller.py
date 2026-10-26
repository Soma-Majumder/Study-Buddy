"""Explicit paid smoke evaluation of controller routing with a fixture Tutor."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from study_buddy.assessment import run_assessment
from study_buddy.controller import start_quiz_session
from study_buddy.lessons import load_lessons
from scripts.validate_quiz import check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Authorize one paid generation plus at most one controller repair')
    parser.add_argument('--capture-response-text', action='store_true', help='Save private model text in a local evaluation artifact')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; this evaluation can make two paid requests')
    report = dict(date=datetime.now(timezone.utc).isoformat(),
                  scope='Live Assessment through real controller; Tutor is a hand-written fixture',
                  prompt_sha256=hashlib.sha256((ROOT / 'backend/prompts/assessment-quiz-system.md').read_bytes()).hexdigest(),
                  generations=[], review='pending', assessment_max_tokens=8192, assessment_timeout_ms=60000, bridge_timeout_seconds=70)

    if args.capture_response_text:
        capture = ROOT / 'backend/evals/results' / (report['date'].replace(':', '-').replace('.', '-') + '-assessment-response-text.jsonl')
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.touch(mode=0o600, exist_ok=False)
        report['response_text_file'] = capture.name
        preload = str(ROOT / 'backend/evals/capture-response.mjs')
        os.environ['NODE_OPTIONS'] = (os.environ.get('NODE_OPTIONS', '') + ' --import=' + json.dumps(preload)).strip()
        os.environ['STUDY_BUDDY_EVAL_CAPTURE_PATH'] = str(capture)

    def tutor(context):
        handoff = json.loads((ROOT / 'schemas/v1/examples/tutor_to_assessment.example.json').read_text())
        handoff['handoff_id'] = context['handoffId']
        handoff['context']['learner_session_id'] = context['sessionId']
        handoff['payload']['explanation_id'] = context['explanationId']
        report['handoff'] = handoff
        return dict(handoff=handoff, repairCount=0)

    def assessment(handoff, *, repair_request=None):
        row = dict(repair_requested=repair_request is not None)
        report['generations'].append(row)
        started = time.monotonic()
        try:
            output = run_assessment(handoff, repair_request=repair_request)
            row['output'] = output
            row['validation_errors'] = check(output, handoff)
            return output
        except Exception as error:
            row['error_stage'] = getattr(error, 'stage', 'evaluation')
            row['error_code'] = getattr(error, 'code', 'adapter_error')
            row['http_status'] = getattr(error, 'http_status', None)
            row['stop_reason'] = getattr(error, 'stop_reason', None)
            raise
        finally:
            row['latency_ms'] = round((time.monotonic() - started) * 1000)

    with sqlite3.connect(':memory:') as conn:
        conn.executescript((ROOT / 'db/schema.sql').read_text())
        load_lessons(conn)
        learner = 'learner_a1b2c3d4e5f6'
        conn.execute('INSERT INTO learners VALUES (?)', (learner,))
        conn.commit()
        result = start_quiz_session(conn, learner, 'lesson_cell_membranes', tutor=tutor, assessment=assessment)
        report['controller_result'] = result
        report['saved_counts'] = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                                  for table in ('quizzes', 'questions', 'answer_keys', 'attempts', 'results')}
        report['session_state'] = conn.execute('SELECT status, learning_retry_count FROM study_sessions').fetchone()
        report['repair_count'] = conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0]
        report['foreign_key_errors'] = conn.execute('PRAGMA foreign_key_check').fetchall()
        private = ('answer_key', 'correct_option', 'rationale')
        report['learner_key_leak'] = any(field in json.dumps(result) for field in private)
        success = result['status'] == 'awaiting_answers'
        report['gates'] = dict(
            valid_quiz_saved=success and report['saved_counts'] == dict(quizzes=1, questions=10, answer_keys=10, attempts=0, results=0),
            learner_keys_private=not report['learner_key_leak'],
            bounded_generations=len(report['generations']) <= 2,
            learning_retry_untouched=report['session_state'][1] == 0,
            foreign_keys_valid=not report['foreign_key_errors'],
        )
    conn.close()
    path = ROOT / 'backend/evals/results' / (report['date'].replace(':', '-').replace('.', '-') + '-assessment-controller.json')
    path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(report=str(path), status=result['status'], generations=len(report['generations']),
                          repair_count=report['repair_count'], gates=report['gates'])))


if __name__ == '__main__':
    main()
