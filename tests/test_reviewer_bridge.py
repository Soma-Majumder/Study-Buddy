"""Real Python -> Node -> fake Claude -> saved-state report validation tests."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from study_buddy.reviewer import run_reviewer, ReviewerFailure
from study_buddy.quality_review import ReviewRejected
from study_buddy.quality_storage import build_private_reviewer_context, store_review_report
from tests import test_quality_storage as fixtures

LEARNER = fixtures.LEARNER


class ReviewerBridgeTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.QualityStorageTests(); self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn = self.helper.conn
        self.call = self.helper.prepare_review()['call_id']
        self.context = build_private_reviewer_context(self.conn, LEARNER, self.call)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / 'calls.jsonl'
        loader = Path(self.temp.name) / 'fake-reviewer-fetch.mjs'
        loader.write_text(r'''import {readFile, appendFile} from 'node:fs/promises';
globalThis.fetch = async (_url, request) => {
  const body = JSON.parse(request.body);
  const data = JSON.parse(body.messages[0].content);
  await appendFile(process.env.REVIEW_BRIDGE_LOG, JSON.stringify({timeout: !!request.signal,
    round: data.controller_context.round, hash: data.controller_context.candidate_hash}) + '\n');
  const mode = process.env.REVIEW_BRIDGE_MODE;
  if (mode === 'provider') throw new Error('private-provider-body');
  if (mode === 'rate') return {ok: false, status: 429};
  if (mode === 'truncated') return {ok: true, json: async () => ({stop_reason: 'max_tokens'})};
  if (mode === 'timeout') return new Promise((_resolve, reject) => request.signal.addEventListener('abort',
    () => reject(new Error('private-abort')), {once: true}));
  const name = mode === 'uncertain' ? 'uncertain' : 'pass';
  const report = JSON.parse(await readFile(`tests/fixtures/quiz_quality/${name}.json`, 'utf8')).report;
  Object.assign(report, data.controller_context);
  for (const q of report.questions) {
    q.concept_id = data.candidate.quiz.questions.find(c => c.position === q.position).concept_id;
    if (q.exactly_one_answer === 'pass') q.defensible_options = [data.candidate.answer_key.find(k => k.position === q.position).correct_option];
  }
  if (mode === 'stale') report.candidate_hash = '0'.repeat(64);
  if (mode === 'shape') report.approved = true;
  if (mode === 'quote') report.questions[0].support_quotes = ['invented evidence quote'];
  const text = mode === 'invalid' ? 'private malformed response' : JSON.stringify(report);
  return {ok: true, json: async () => ({stop_reason: 'end_turn', content: [{type: 'text', text}],
    model: 'offline-reviewer', usage: {input_tokens: 1, output_tokens: 1}})};
};
''')
        env = patch.dict(os.environ, {'NODE_OPTIONS': f'--import={loader}', 'ANTHROPIC_API_KEY': 'fake-reviewer-key',
            'REVIEW_BRIDGE_LOG': str(self.log), 'REVIEW_BRIDGE_MODE': 'valid'})
        env.start(); self.addCleanup(env.stop)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_report_crosses_bridge_and_saved_state_validator_derives_approval(self):
        report = run_reviewer(self.context)
        self.assertEqual(report['candidate_hash'], self.context['controller_context']['candidate_hash'])
        # Provider completion alone must not change operation state or publish.
        self.assertEqual(self.conn.execute('SELECT stage FROM quiz_quality_operations').fetchone()[0], 'reviewing')
        result = store_review_report(self.conn, LEARNER, self.call, report)
        self.assertEqual(result['decision'], 'pass')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 0)
        self.assertEqual(len(self.calls()), 1)

    def test_uncertainty_remains_blocking_after_real_bridge(self):
        os.environ['REVIEW_BRIDGE_MODE'] = 'uncertain'
        report = run_reviewer(self.context)
        result = store_review_report(self.conn, LEARNER, self.call, report)
        self.assertEqual(result['decision'], 'blocked')
        self.assertEqual(result['operation']['stage'], 'failed')
        self.assertEqual(len(self.calls()), 1)

    def test_structurally_valid_invented_evidence_still_fails_python_validation(self):
        os.environ['REVIEW_BRIDGE_MODE'] = 'quote'
        report = run_reviewer(self.context)
        with self.assertRaises(ReviewRejected): store_review_report(self.conn, LEARNER, self.call, report)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 0)
        self.assertEqual(len(self.calls()), 1)

    def test_bad_input_never_reaches_provider(self):
        with self.assertRaises(ReviewerFailure) as raised: run_reviewer({})
        self.assertEqual(raised.exception.code, 'invalid_input')
        self.assertEqual(self.calls(), [])

    def test_safe_failures_cross_real_bridge_without_retry(self):
        for mode, code in [('provider', 'transport_error'), ('rate', 'rate_limited'),
                           ('truncated', 'output_truncated'), ('stale', 'mismatched_report'),
                           ('shape', 'invalid_report'), ('invalid', 'invalid_json')]:
            os.environ['REVIEW_BRIDGE_MODE'] = mode
            before = len(self.calls())
            with self.assertRaises(ReviewerFailure) as raised: run_reviewer(self.context)
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn('private', str(raised.exception))
            self.assertEqual(len(self.calls()), before + 1)
            if mode == 'rate': self.assertEqual(raised.exception.http_status, 429)
            if mode == 'truncated': self.assertEqual(raised.exception.stop_reason, 'max_tokens')

    def test_shorter_request_deadline_reaches_real_adapter(self):
        os.environ['REVIEW_BRIDGE_MODE'] = 'timeout'
        with self.assertRaises(ReviewerFailure) as raised:
            run_reviewer(self.context, provider_timeout_ms=5, process_timeout_seconds=5)
        self.assertEqual(raised.exception.code, 'request_timeout')
        self.assertEqual(len(self.calls()), 1)

    def test_invalid_timeout_allowances_fail_before_process(self):
        for options in [dict(provider_timeout_ms=True), dict(provider_timeout_ms=60001),
                        dict(provider_timeout_ms=0), dict(process_timeout_seconds=60),
                        dict(process_timeout_seconds=float('nan')), dict(process_timeout_seconds=71)]:
            with patch('study_buddy.reviewer.subprocess.run') as run:
                with self.assertRaises(ReviewerFailure) as raised: run_reviewer(self.context, **options)
                self.assertEqual(raised.exception.code, 'invalid_timeout'); run.assert_not_called()

    def test_process_timeout_unavailability_and_broken_envelopes_are_safe(self):
        for outcome, code in [(subprocess.TimeoutExpired('private-cmd', 70, output='private-body'), 'process_timeout'),
                              (OSError('private-path'), 'process_unavailable'),
                              (SimpleNamespace(returncode=0, stdout='private-body'), 'invalid_envelope'),
                              (SimpleNamespace(returncode=0, stdout='[]'), 'invalid_envelope'),
                              (SimpleNamespace(returncode=0, stdout='{"ok":true,"result":{"output":null}}'), 'invalid_envelope'),
                              (SimpleNamespace(returncode=1, stdout='{"ok":true,"result":{"output":{}}}'), 'invalid_envelope')]:
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with patch('study_buddy.reviewer.subprocess.run', **kwargs):
                with self.assertRaises(ReviewerFailure) as raised: run_reviewer(self.context)
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn('private', str(raised.exception))

    def test_unknown_and_nonstring_diagnostics_are_sanitized(self):
        for value in ['private', [], {}]:
            body = dict(ok=False, error=dict(stage=value, code=value, stop_reason=value, http_status=value))
            with patch('study_buddy.reviewer.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout=json.dumps(body))):
                with self.assertRaises(ReviewerFailure) as raised: run_reviewer(self.context)
                error = raised.exception
                self.assertEqual((error.stage, error.code, error.http_status, error.stop_reason),
                                 ('provider adapter', 'adapter_error', None, None))

    def test_bridge_passes_only_context_and_request_timeout_over_stdin(self):
        result = SimpleNamespace(returncode=0, stdout=json.dumps(dict(ok=True, result=dict(output=dict(report='fixture')))))
        with patch('study_buddy.reviewer.subprocess.run', return_value=result) as run:
            run_reviewer(self.context, provider_timeout_ms=12000, process_timeout_seconds=13)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs['timeout'], 13)
        self.assertEqual(json.loads(kwargs['input']), dict(context=self.context, provider_timeout_ms=12000))
        self.assertNotIn('fake-reviewer-key', kwargs['input'])
