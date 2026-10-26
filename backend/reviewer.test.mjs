import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {runReviewer} from './reviewer.mjs';
import {reviewerResponseSchema, matchesReviewerSchema} from './reviewer-schema.mjs';

const load = async name => JSON.parse(await readFile(new URL(`../tests/fixtures/quiz_quality/${name}.json`, import.meta.url), 'utf8'));
const fixture = await load('context');
const pass = (await load('pass')).report;
function input() {
  const candidate = structuredClone(fixture.candidate);
  const canonical = JSON.stringify(candidate);
  const {questions, quiz_review, ...header} = structuredClone(pass);
  header.candidate_hash = createHash('sha256').update('study-buddy-private-quiz/v1\n' + canonical).digest('hex');
  return {controller_context: header, candidate, candidate_canonical_json: canonical,
    teaching: {explanation_text: fixture.handoff.payload.explanation_text,
      concept_ids: fixture.handoff.payload.concepts.map(c => c.concept_id), focus_concept_ids: []},
    sources: structuredClone(fixture.handoff.sources)};
}
const reportFor = (context, report = pass) => ({...structuredClone(report), ...context.controller_context});
const response = (text, extra = {}) => ({ok: true, json: async () => ({stop_reason: 'end_turn',
  content: [{type: 'text', text}], model: 'test-reviewer', usage: {input_tokens: 100, output_tokens: 200}, ...extra})});
const options = fetchImpl => ({apiKey: 'fake-key', model: 'test-reviewer', fetchImpl});

test('sends one isolated review with fixed grammar, bounded defaults, model and usage', async () => {
  const context = input(); const report = reportFor(context); let calls = 0;
  const result = await runReviewer(context, {claudeOptions: {...options(async (_url, request) => {
    calls++;
    const body = JSON.parse(request.body);
    assert.equal(body.max_tokens, 4096); assert.equal(body.output_config.effort, 'low');
    assert.equal(body.output_config.format.type, 'json_schema');
    assert.deepEqual(body.output_config.format.schema, reviewerResponseSchema);
    assert.equal(body.messages.length, 1);
    assert.deepEqual(JSON.parse(body.messages[0].content).candidate.answer_key, context.candidate.answer_key);
    assert.match(body.system, /Reviewer/);
    return response(JSON.stringify(report));
  }), outputSchema: {type: 'string'}}});
  assert.equal(calls, 1); assert.deepEqual(result.output, report);
  assert.equal(result.model, 'test-reviewer'); assert.equal(result.usage.output_tokens, 200);
  assert.equal(result.decision, undefined);
});

test('invalid inputs stop before a request', async () => {
  await assert.rejects(runReviewer({}, {claudeOptions: options(() => assert.fail('No call'))}),
    e => e.code === 'invalid_input' && e.stage === 'input preparation');
});

test('malformed and noncanonical reports fail once without repair or raw response leakage', async () => {
  for (const text of ['private malformed response', 'null', '{}', JSON.stringify({...reportFor(input()), approved: true}),
    JSON.stringify({...reportFor(input()), questions: []})]) {
    let calls = 0;
    await assert.rejects(runReviewer(input(), {claudeOptions: options(async () => {
      calls++; return response(text);
    })}), e => ['invalid_json', 'invalid_report'].includes(e.code) && e.stage === 'response validation'
      && !JSON.stringify(e).includes('private malformed response'));
    assert.equal(calls, 1);
  }
});

test('rejects stale bindings, duplicate positions and changed concepts', async () => {
  for (const mutate of [r => r.candidate_hash = '0'.repeat(64), r => r.candidate_version = 1,
    r => r.session_id = 'session_other', r => r.questions[0].position = 2,
    r => r.questions[0].concept_id = 'concept_other']) {
    const c = input(); const r = reportFor(c); mutate(r);
    await assert.rejects(runReviewer(c, {claudeOptions: options(async () => response(JSON.stringify(r)))}),
      e => e.code === 'mismatched_report');
  }
});

test('failed and uncertain judgments are returned for controller policy, not retried or promoted', async () => {
  for (const name of ['ambiguous_answers', 'inaccurate_rationale', 'uncertain']) {
    const c = input(); const r = reportFor(c, (await load(name)).report); let calls = 0;
    const result = await runReviewer(c, {claudeOptions: options(async () => {calls++; return response(JSON.stringify(r));})});
    assert.deepEqual(result.output, r); assert.equal(calls, 1); assert.equal(result.decision, undefined);
  }
});

test('transport, truncation and HTTP errors have safe diagnostics and no retry', async () => {
  for (const [fetchImpl, code] of [
    [async () => {throw new Error('secret-provider-text');}, 'transport_error'],
    [async () => response('secret-provider-text', {stop_reason: 'max_tokens'}), 'output_truncated'],
    [async () => ({ok: false, status: 429, json: () => assert.fail('Do not read error body')}), 'rate_limited'],
  ]) {
    let calls = 0;
    await assert.rejects(runReviewer(input(), {claudeOptions: options(async (...args) => {calls++; return fetchImpl(...args);})}),
      e => e.code === code && e.stage === 'model call' && !JSON.stringify(e).includes('secret-provider-text'));
    assert.equal(calls, 1);
  }
});

test('caller mutation during fetch cannot replace expected bindings', async () => {
  const c = input(); const original = reportFor(c);
  await assert.rejects(runReviewer(c, {claudeOptions: options(async () => {
    c.controller_context.candidate_hash = '0'.repeat(64);
    return response(JSON.stringify({...original, candidate_hash: c.controller_context.candidate_hash}));
  })}), e => e.code === 'mismatched_report');
});

test('canonical validation retains constraints omitted from provider grammar', () => {
  const r = reportFor(input());
  assert.equal(matchesReviewerSchema(r), true);
  assert.equal(reviewerResponseSchema.properties.questions.minItems, undefined);
  r.questions.pop(); assert.equal(matchesReviewerSchema(r), false);
});

test('deadline override aborts a stalled call once', async () => {
  let calls = 0;
  await assert.rejects(runReviewer(input(), {claudeOptions: {...options(async (_url, {signal}) => {
    calls++; return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('secret-abort')), {once: true}));
  }), timeoutMs: 5}}), e => e.code === 'request_timeout' && e.stage === 'model call');
  assert.equal(calls, 1);
});
