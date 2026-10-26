import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runAssessmentQuiz } from './assessment.mjs';

const fixture = async name => JSON.parse(await readFile(new URL(
  `../schemas/v1/examples/${name}.example.json`, import.meta.url), 'utf8'));
const response = text => ({ok: true, json: async () => ({
  stop_reason: 'end_turn', content: [{type: 'text', text}], model: 'test-model',
  usage: {input_tokens: 20, output_tokens: 10},
})});
const options = fetchImpl => ({apiKey: 'fake-test-key', model: 'test-model', fetchImpl});

test('Assessment packages the handoff and makes exactly one provider request', async () => {
  const handoff = await fixture('tutor_to_assessment');
  const quiz = await fixture('quiz_output');
  let calls = 0;
  const result = await runAssessmentQuiz(handoff, {claudeOptions: options(async (_url, request) => {
    calls++;
    const body = JSON.parse(request.body);
    assert.match(body.system, /Assessment/);
    assert.equal(body.max_tokens, 8192);
    assert.deepEqual(body.output_config, {effort: 'low'});
    assert.equal(body.thinking, undefined);
    const data = JSON.parse(body.messages[0].content);
    assert.equal(data.handoff.handoff_id, handoff.handoff_id);
    assert.equal(data.repair_request, undefined);
    return response(JSON.stringify(quiz));
  })});
  assert.equal(calls, 1);
  assert.deepEqual(result.output, quiz);
  assert.equal(result.usage.output_tokens, 10);
});

test('invalid input stops before any provider request', async () => {
  await assert.rejects(runAssessmentQuiz({}, {claudeOptions: options(() => assert.fail('No request expected'))}),
    {name: 'AssessmentRunError', message: 'Assessment failed during input preparation.'});
});

test('malformed JSON, local failure, and schema-invalid output do not trigger local repair', async () => {
  for (const text of ['not JSON', '{"error":{"code":"insufficient_quiz_content"}}', '{"quiz":{}}']) {
    let calls = 0;
    const result = await runAssessmentQuiz(await fixture('tutor_to_assessment'), {
      claudeOptions: options(async () => { calls++; return response(text); }),
    });
    assert.equal(calls, 1);
    assert.deepEqual(result.output, text === 'not JSON' ? null : JSON.parse(text));
  }
});

test('controller repair uses original data and fixed instructions without echoing arbitrary prose', async () => {
  let calls = 0;
  const handoff = await fixture('tutor_to_assessment.retry');
  await runAssessmentQuiz(handoff, {repairRequest: 'untrusted raw model text',
    claudeOptions: options(async (_url, request) => {
      calls++;
      assert.ok(!request.body.includes('untrusted raw model text'));
      const data = JSON.parse(JSON.parse(request.body).messages[0].content);
      assert.equal(data.handoff.context.round, 'retry');
      assert.deepEqual(data.handoff.payload.focus_concept_ids, handoff.payload.focus_concept_ids);
      assert.match(data.repair_request, /controller-authorized technical repair/);
      return response('{}');
    }),
  });
  assert.equal(calls, 1);
});

test('provider failure stops after one request and does not expose its message', async () => {
  let calls = 0;
  await assert.rejects(runAssessmentQuiz(await fixture('tutor_to_assessment'), {
    claudeOptions: options(async () => {calls++; throw new Error('secret-provider-body');}),
  }), {name: 'AssessmentRunError', message: 'Assessment failed during model call.'});
  assert.equal(calls, 1);
});

test('invalid repair metadata stops before a provider request', async () => {
  await assert.rejects(runAssessmentQuiz(await fixture('tutor_to_assessment'), {
    repairRequest: true, claudeOptions: options(() => assert.fail('No request expected')),
  }), /input preparation/);
});


test('Assessment preserves safe truncation and API diagnostics without raw provider content', async () => {
  const h = await fixture('tutor_to_assessment');
  await assert.rejects(runAssessmentQuiz(h, {claudeOptions: options(async () => ({ok: true,
    json: async () => ({stop_reason: 'max_tokens', content: [{type: 'text', text: 'private-generated-text'}]})}))}),
  error => error.stage === 'model call' && error.code === 'output_truncated' && error.stopReason === 'max_tokens'
    && !JSON.stringify(error).includes('private-generated-text'));
});


test('Assessment defaults to a 60-second deadline and still aborts a stalled request', async () => {
  const realSetTimeout = globalThis.setTimeout;
  const realClearTimeout = globalThis.clearTimeout;
  let delay;
  let fire;
  globalThis.setTimeout = (callback, ms) => { delay = ms; fire = callback; return 1; };
  globalThis.clearTimeout = () => {};
  try {
    await assert.rejects(runAssessmentQuiz(await fixture('tutor_to_assessment'), {
      claudeOptions: options(async (_url, {signal}) => new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new Error('private-abort-error')), {once: true});
        fire();
      })),
    }), error => error.code === 'request_timeout' && error.stage === 'model call');
    assert.equal(delay, 60_000);
  } finally {
    globalThis.setTimeout = realSetTimeout;
    globalThis.clearTimeout = realClearTimeout;
  }
});
