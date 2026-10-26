"""Real Python -> Node -> fake fetch -> controller validation/save tests."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from study_buddy.assessment import run_assessment, AssessmentFailure
from study_buddy.controller import start_quiz_session
from tests import test_controller as fixtures

LEARNER = fixtures.LEARNER

ROOT = Path(__file__).resolve().parent.parent


class AssessmentBridgeTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.ControllerTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / 'calls.jsonl'
        loader = Path(self.temp.name) / 'fake-fetch.mjs'
        loader.write_text(r'''import {readFile, appendFile} from 'node:fs/promises';
globalThis.fetch = async (_url, request) => {
  const data = JSON.parse(JSON.parse(request.body).messages[0].content);
  await appendFile(process.env.BRIDGE_TEST_LOG, JSON.stringify({repair: !!data.repair_request}) + '\n');
  if (process.env.BRIDGE_TEST_MODE === 'provider') throw new Error('private-provider-body');
  if (process.env.BRIDGE_TEST_MODE === 'truncated') return {ok: true, json: async () => ({stop_reason: 'max_tokens'})};
  if (process.env.BRIDGE_TEST_MODE === 'rate') return {ok: false, status: 429};
  let text = 'not JSON';
  if (process.env.BRIDGE_TEST_MODE === 'valid' || (process.env.BRIDGE_TEST_MODE === 'repair' && data.repair_request)) {
    const quiz = JSON.parse(await readFile('schemas/v1/examples/quiz_output.example.json', 'utf8'));
    quiz.handoff_id = data.handoff.handoff_id;
    quiz.explanation_id = data.handoff.payload.explanation_id;
    quiz.round = data.handoff.context.round;
    text = JSON.stringify({result: quiz});
  }
  return {ok: true, json: async () => ({stop_reason: 'end_turn', content: [{type: 'text', text}],
    model: 'offline-fixture', usage: {input_tokens: 1, output_tokens: 1}})};
};
''')
        env = patch.dict(os.environ, {
            'NODE_OPTIONS': f'--import={loader}', 'ANTHROPIC_API_KEY': 'fake-bridge-key',
            'BRIDGE_TEST_LOG': str(self.log), 'BRIDGE_TEST_MODE': 'valid',
        })
        env.start()
        self.addCleanup(env.stop)

    def start(self):
        return start_quiz_session(self.helper.conn, LEARNER, 'lesson_cell_membranes', tutor=self.helper.tutor)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_default_bridge_saves_ten_private_keys_and_returns_safe_quiz(self):
        result = self.start()
        self.assertEqual(result['status'], 'awaiting_answers')
        self.assertEqual(len(result['quiz']['questions']), 10)
        self.assertEqual(self.helper.conn.execute('SELECT COUNT(*) FROM answer_keys').fetchone()[0], 10)
        self.assertEqual(self.calls(), [{'repair': False}])
        for private in ('answer_key', 'correct_option', 'rationale'):
            self.assertNotIn(private, json.dumps(result))

    def test_malformed_generation_uses_only_controller_reserved_repair(self):
        os.environ['BRIDGE_TEST_MODE'] = 'repair'
        result = self.start()
        self.assertEqual(result['status'], 'awaiting_answers')
        self.assertEqual(self.calls(), [{'repair': False}, {'repair': True}])
        self.assertEqual(self.helper.conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0], 1)

    def test_two_invalid_generations_fail_without_quiz_or_learning_retry(self):
        os.environ['BRIDGE_TEST_MODE'] = 'invalid'
        result = self.start()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.helper.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 0)
        self.assertEqual(self.helper.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)

    def test_provider_failure_stops_once_without_private_error_text(self):
        os.environ['BRIDGE_TEST_MODE'] = 'provider'
        result = self.start()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.calls()), 1)
        self.assertNotIn('private-provider-body', json.dumps(result))

    def test_bad_input_stops_in_real_node_before_fetch(self):
        with self.assertRaises(AssessmentFailure) as raised:
            run_assessment({})
        self.assertEqual(raised.exception.stage, 'input preparation')
        self.assertEqual(self.calls(), [])

    def test_process_timeout_and_broken_envelope_are_safe(self):
        import subprocess
        from types import SimpleNamespace
        for outcome in (subprocess.TimeoutExpired('node', 70), SimpleNamespace(returncode=0, stdout='{}')):
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with patch('study_buddy.assessment.subprocess.run', **kwargs):
                with self.assertRaises(AssessmentFailure):
                    run_assessment({})

    def test_safe_diagnostics_cross_real_python_node_boundary(self):
        h = json.loads((ROOT / 'schemas/v1/examples/tutor_to_assessment.example.json').read_text())
        for mode, code, status, reason in [('truncated', 'output_truncated', None, 'max_tokens'),
                                           ('rate', 'rate_limited', 429, None),
                                           ('provider', 'transport_error', None, None)]:
            os.environ['BRIDGE_TEST_MODE'] = mode
            with self.assertRaises(AssessmentFailure) as raised:
                run_assessment(h)
            self.assertEqual((raised.exception.code, raised.exception.http_status, raised.exception.stop_reason),
                             (code, status, reason))
            self.assertNotIn('private-provider-body', str(raised.exception))

    def test_unknown_process_diagnostics_are_not_forwarded(self):
        from types import SimpleNamespace
        envelope = dict(ok=False, error=dict(stage='private-stage', code='private-code',
                                             http_status='private-status', stop_reason='private-reason'))
        with patch('study_buddy.assessment.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout=json.dumps(envelope))):
            with self.assertRaises(AssessmentFailure) as raised:
                run_assessment({})
        self.assertEqual((raised.exception.stage, raised.exception.code, raised.exception.http_status, raised.exception.stop_reason),
                         ('provider adapter', 'adapter_error', None, None))

    def test_python_bridge_deadline_exceeds_assessment_request_deadline(self):
        from types import SimpleNamespace
        with patch('study_buddy.assessment.subprocess.run', return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(dict(ok=True, result=dict(output=None))))) as run:
            self.assertIsNone(run_assessment({}))
        self.assertEqual(run.call_args.kwargs['timeout'], 70)
