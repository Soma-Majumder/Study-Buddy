"""Real Python -> Node -> fake fetch -> controller validation/save tests for Assessment feedback."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from study_buddy.assessment import AssessmentFailure
from study_buddy.controller import explain_results
from study_buddy.feedback_runner import run_feedback
from tests import test_feedback as fixtures

LEARNER = fixtures.LEARNER


class FeedbackBridgeTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.FeedbackTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / 'calls.jsonl'
        loader = Path(self.temp.name) / 'fake-fetch.mjs'
        loader.write_text(r'''import {appendFile} from 'node:fs/promises';
globalThis.fetch = async (_url, request) => {
  const data = JSON.parse(JSON.parse(request.body).messages[0].content);
  await appendFile(process.env.BRIDGE_TEST_LOG, JSON.stringify({repair: !!data.repair_request, positions: data.results.length}) + '\n');
  if (process.env.BRIDGE_TEST_MODE === 'rate') return {ok: false, status: 429};
  let text = 'not JSON';
  const mode = process.env.BRIDGE_TEST_MODE;
  if (mode === 'valid' || (mode === 'repair' && data.repair_request)) {
    text = JSON.stringify({schema_version: '1.0', quiz_id: data.quiz_id, attempt_id: data.attempt_id,
      feedback: data.results.map(r => ({position: r.position, explanation: r.rationale})),
      weak_concepts: data.weak_concepts, summary: 'Review the missed concepts.'});
  }
  return {ok: true, json: async () => ({stop_reason: 'end_turn', content: [{type: 'text', text}],
    model: 'offline-fixture', usage: {input_tokens: 1, output_tokens: 1}})};
};
''')
        env = patch.dict(os.environ, {'NODE_OPTIONS': f'--import={loader}', 'ANTHROPIC_API_KEY': 'fake-bridge-key',
                                      'BRIDGE_TEST_LOG': str(self.log), 'BRIDGE_TEST_MODE': 'valid'})
        env.start()
        self.addCleanup(env.stop)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def explain(self):
        return explain_results(self.helper.conn, LEARNER, self.helper.attempt_id)

    def test_default_bridge_saves_validated_feedback_and_keeps_the_score(self):
        result = self.explain()
        self.assertEqual((result['operation_status'], result['status'], result['score']), ('ready', 'grading', 6))
        self.assertEqual(self.calls(), [{'repair': False, 'positions': 10}])
        self.assertEqual(len(result['feedback']['feedback']), 10)
        self.assertNotIn('score', result['feedback'])

    def test_malformed_feedback_uses_only_the_one_reserved_repair(self):
        os.environ['BRIDGE_TEST_MODE'] = 'repair'
        result = self.explain()
        self.assertEqual(result['operation_status'], 'ready')
        self.assertEqual(self.calls(), [{'repair': False, 'positions': 10}, {'repair': True, 'positions': 10}])

    def test_two_invalid_responses_fail_without_changing_the_saved_result(self):
        os.environ['BRIDGE_TEST_MODE'] = 'invalid'
        result = self.explain()
        self.assertEqual((result['operation_status'], result['status']), ('failed', 'failed'))
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.helper.conn.execute('SELECT score FROM results').fetchone()[0], 6)

    def test_provider_error_keeps_a_fixed_code_and_is_not_retried(self):
        os.environ['BRIDGE_TEST_MODE'] = 'rate'
        with self.assertRaises(AssessmentFailure) as caught:
            run_feedback(dict(task='explain_verified_results', quiz_id='quiz_aaaaaaaaaaaa', attempt_id='attempt_bbbbbbbbbbbb',
                              explanation_text='x', graded=dict(items=[dict(position=i, concept_id='c', selected='a', correct_option='a',
                                                                            is_correct=True, rationale='r') for i in range(1, 11)],
                                                                weak_concepts=[]),
                              sources=[dict(lesson_id='l', lesson_version=1, url='u', license='l', attribution='a')]))
        self.assertEqual(caught.exception.code, 'rate_limited')
        self.assertEqual(len(self.calls()), 1)

    def test_invalid_context_never_reaches_the_provider(self):
        with self.assertRaises(AssessmentFailure) as caught:
            run_feedback({})
        self.assertEqual(caught.exception.code, 'invalid_input')
        self.assertEqual(self.calls(), [])
