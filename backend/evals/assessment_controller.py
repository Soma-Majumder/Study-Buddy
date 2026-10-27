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



USAGE_FIELDS = ('input_tokens', 'output_tokens', 'cache_creation_input_tokens',
                'cache_read_input_tokens', 'thinking_tokens')


def usage_totals(generations):
    # Missing metadata is unknown, never assumed zero. Counts include invalid
    # generations and repairs when the provider supplied response metadata.
    totals = {}
    for field in USAGE_FIELDS:
        values = [g.get('token_usage', {}).get(field) for g in generations]
        totals[field] = sum(values) if values and all(type(v) is int and v >= 0 for v in values) else None
    output, thinking = totals['output_tokens'], totals['thinking_tokens']
    totals['thinking_share_of_output'] = thinking / output if output and thinking is not None and thinking <= output else None
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Authorize paid model calls')
    parser.add_argument('--quality-gate', action='store_true', help='Authorize the full gated flow, at most five Assessment/Reviewer calls')
    capture_flags = parser.add_mutually_exclusive_group()
    capture_flags.add_argument('--capture-response-text', action='store_true', help='Save private model text in a local evaluation artifact')
    capture_flags.add_argument('--capture-token-usage', action='store_true', help='Save usage/stop reason/text length only, including truncated responses')
    args = parser.parse_args()
    if not args.live or not args.quality_gate:
        parser.error('--live --quality-gate are required; the full flow can make up to five paid requests')
    os.environ['STUDY_BUDDY_ALLOW_PAID_CLAUDE'] = 'true'
    report = dict(date=datetime.now(timezone.utc).isoformat(),
                  scope='Live Assessment through real controller; Tutor is a hand-written fixture',
                  prompt_sha256=hashlib.sha256((ROOT / 'backend/prompts/assessment-quiz-system.md').read_bytes()).hexdigest(),
                  quality_reference_sha256=hashlib.sha256((ROOT / 'backend/prompts/assessment-quality-reference.md').read_bytes()).hexdigest(),
                  generations=[], review='pending', assessment_structured_output=True, assessment_effort='low', assessment_max_tokens=8192, assessment_timeout_ms=60000, bridge_timeout_seconds=70)

    capture_enabled = args.capture_response_text or args.capture_token_usage
    if capture_enabled:
        capture = ROOT / 'backend/evals/results' / (report['date'].replace(':', '-').replace('.', '-') + '-assessment-response-text.jsonl')
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.touch(mode=0o600, exist_ok=False)
        if args.capture_token_usage:
            capture = capture.rename(capture.with_name(capture.name.replace('response-text', 'token-usage')))
        report['response_text_file' if args.capture_response_text else 'token_usage_file'] = capture.name
        preload = str(ROOT / 'backend/evals' / ('capture-response.mjs' if args.capture_response_text else 'capture-usage.mjs'))
        os.environ['NODE_OPTIONS'] = (os.environ.get('NODE_OPTIONS', '') + ' --import=' + json.dumps(preload)).strip()
        os.environ['STUDY_BUDDY_EVAL_CAPTURE_PATH'] = str(capture)

    def tutor(context):
        handoff = json.loads((ROOT / 'schemas/v1/examples/tutor_to_assessment.example.json').read_text())
        handoff['handoff_id'] = context['handoffId']
        handoff['context']['learner_session_id'] = context['sessionId']
        handoff['payload']['explanation_id'] = context['explanationId']
        report['handoff'] = handoff
        return dict(handoff=handoff, repairCount=0)

    def tagged_call(phase, runner, *inputs, **kwargs):
        previous = os.environ.get('STUDY_BUDDY_EVAL_PHASE')
        os.environ['STUDY_BUDDY_EVAL_PHASE'] = phase
        try:
            return runner(*inputs, **kwargs)
        finally:
            if previous is None:
                os.environ.pop('STUDY_BUDDY_EVAL_PHASE', None)
            else:
                os.environ['STUDY_BUDDY_EVAL_PHASE'] = previous

    def reviewer(context, **kwargs):
        from study_buddy.reviewer import run_reviewer
        phase = 'review_revision' if context['controller_context']['candidate_version'] else 'review'
        return tagged_call(phase, run_reviewer, context, **kwargs)

    def revision(context, **kwargs):
        from study_buddy.assessment import run_assessment_revision
        return tagged_call('revision', run_assessment_revision, context, **kwargs)

    def assessment(handoff, *, repair_request=None, **timeouts):
        row = dict(repair_requested=repair_request is not None)
        report['generations'].append(row)
        started = time.monotonic()
        try:
            output = tagged_call('assessment_repair' if repair_request else 'assessment_generation', run_assessment, handoff, repair_request=repair_request, **timeouts)
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
            if capture_enabled:
                try:
                    records = [json.loads(line) for line in capture.read_text().splitlines()]
                    if args.capture_token_usage:
                        records = [r for r in records if r.get('phase') in ('assessment_generation', 'assessment_repair')]
                    index = len(report['generations']) - 1
                    if index < len(records):
                        row['token_usage'] = records[index].get('usage', {})
                except (OSError, ValueError):
                    row['usage_capture_error'] = True

    with sqlite3.connect(':memory:') as conn:
        conn.executescript((ROOT / 'db/schema.sql').read_text())
        load_lessons(conn)
        learner = 'learner_a1b2c3d4e5f6'
        conn.execute('INSERT INTO learners VALUES (?)', (learner,))
        conn.commit()
        result = start_quiz_session(conn, learner, 'lesson_cell_membranes', tutor=tutor, assessment=assessment, reviewer=reviewer, revision_assessment=revision)
        report['controller_result'] = result
        report['saved_counts'] = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                                  for table in ('quizzes', 'questions', 'answer_keys', 'attempts', 'results')}
        report['session_state'] = conn.execute('SELECT status, learning_retry_count FROM study_sessions').fetchone()
        report['quality_call_count'] = conn.execute('SELECT COUNT(*) FROM quiz_quality_calls').fetchone()[0]
        report['quality_publication_count'] = conn.execute('SELECT COUNT(*) FROM quiz_quality_publications').fetchone()[0]
        report['quality_operations'] = conn.execute('SELECT stage,candidate_version,revision_count,review_count,failure_code FROM quiz_quality_operations').fetchall()
        report['repair_count'] = conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0]
        report['foreign_key_errors'] = conn.execute('PRAGMA foreign_key_check').fetchall()
        private = ('answer_key', 'correct_option', 'rationale')
        report['learner_key_leak'] = any(field in json.dumps(result) for field in private)
        success = result['status'] == 'awaiting_answers'
        report['gates'] = dict(
            valid_quiz_saved=success and report['saved_counts'] == dict(quizzes=1, questions=10, answer_keys=10, attempts=0, results=0),
            learner_keys_private=not report['learner_key_leak'],
            bounded_generations=len(report['generations']) <= 2,
            bounded_quality_calls=report['quality_call_count'] <= 5,
            reviewed_publication=not success or report['quality_publication_count'] == 1,
            learning_retry_untouched=report['session_state'][1] == 0,
            foreign_keys_valid=not report['foreign_key_errors'],
        )
    conn.close()
    report['generation_token_usage_totals'] = usage_totals(report['generations'])
    report['token_usage_totals'] = None
    if capture_enabled:
        records = [json.loads(line) for line in capture.read_text().splitlines()]
        report['captured_response_count'] = len(records)
        if args.capture_token_usage:
            report['token_usage_records'] = records
            report['token_usage_by_phase'] = {phase: usage_totals([dict(token_usage=r.get('usage', {})) for r in records if r.get('phase') == phase])
                for phase in sorted({r.get('phase', 'unknown') for r in records})}
        report['token_usage_totals'] = usage_totals([dict(token_usage=r.get('usage', {})) for r in records])
        report['usage_scope'] = 'Captured successful HTTP responses across all phases; failed HTTP calls may have no usage metadata.'
    path = ROOT / 'backend/evals/results' / (report['date'].replace(':', '-').replace('.', '-') + '-assessment-controller.json')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(report=str(path), status=result['status'], generations=len(report['generations']),
                          repair_count=report['repair_count'], gates=report['gates'])))


if __name__ == '__main__':
    main()
