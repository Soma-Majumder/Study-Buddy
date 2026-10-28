import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runTutor, TutorRunError } from './tutor.mjs';

const context = {
  lessonId: 'lesson_cell_membranes', sessionId: 'session_q8w1e5r2t6y4',
  handoffId: 'handoff_a1b2c3d4e5f6', explanationId: 'expl_z9y8x7w6v5u4',
};
const fixture = JSON.parse(await readFile(new URL('../schemas/v1/examples/tutor_to_assessment.example.json', import.meta.url), 'utf8'));

function fakeClaude(invalidCalls = 0) {
  let calls = 0;
  return {
    count: () => calls,
    options: {allowPaidCalls: true, apiKey: 'fake-test-key', fetchImpl: async (_url, options) => {
      calls++;
      const input = JSON.parse(JSON.parse(options.body).messages[0].content);
      const h = structuredClone(fixture);
      h.handoff_id = input.controller_context.handoff_id;
      h.context.learner_session_id = input.controller_context.learner_session_id;
      h.payload.explanation_id = input.controller_context.explanation_id;
      h.sources = input.sources;
      if (calls <= invalidCalls) h.payload.concepts[0].concept_id = 'c_invented';
      return {ok: true, json: async () => ({
        stop_reason: 'end_turn', content: [{type: 'text', text: JSON.stringify(h)}],
        model: 'test-model', usage: {input_tokens: 100, output_tokens: 50},
      })};
    }},
  };
}

test('connects input, one model call, and validation into a usable handoff', async () => {
  const fake = fakeClaude();
  const result = await runTutor(context, fake.options);
  assert.equal(fake.count(), 1);
  assert.equal(result.handoff.handoff_id, context.handoffId);
  assert.equal(result.handoff.payload.lesson_id, context.lessonId);
  assert.equal(result.handoff.payload.concepts.length, 7);
  assert.equal(result.model, 'test-model');
  assert.deepEqual(result.usage, {input_tokens: 100, output_tokens: 50});
  assert.equal(result.handoff.usage, undefined);
});

test('input failure stops before making a model call', async () => {
  const fake = fakeClaude();
  await assert.rejects(runTutor({...context, lessonId: 'lesson_missing'}, fake.options),
    error => error instanceof TutorRunError && error.stage === 'input preparation');
  assert.equal(fake.count(), 0);
});

test('transport failure stops without returning a handoff', async () => {
  const fake = fakeClaude();
  await assert.rejects(runTutor(context, {...fake.options, allowPaidCalls: true, apiKey: ''}),
    error => error instanceof TutorRunError && error.stage === 'model call');
  assert.equal(fake.count(), 0);
});

test('two invalid outputs exhaust the repair budget and stop', async () => {
  const fake = fakeClaude(2);
  await assert.rejects(runTutor(context, fake.options),
    error => {
      assert.ok(error instanceof TutorRunError);
      assert.equal(error.stage, 'output validation');
      assert.equal(error.repairCount, 1);
      assert.deepEqual(error.usage, {input_tokens: 200, output_tokens: 100});
      return true;
    });
  assert.equal(fake.count(), 2);
});

test('one invalid output is repaired once and validated against the original IDs', async () => {
  const fake = fakeClaude(1);
  const state = {count: 0};
  const result = await runTutor(context, fake.options, state);
  assert.equal(fake.count(), 2);
  assert.equal(state.count, 1);
  assert.equal(result.repairCount, 1);
  assert.deepEqual(result.usage, {input_tokens: 200, output_tokens: 100});
  assert.equal(result.handoff.handoff_id, context.handoffId);
  assert.equal(result.handoff.context.round, 'first');
});

test('an already consumed repair budget cannot be spent again', async () => {
  const fake = fakeClaude(1);
  await assert.rejects(runTutor(context, fake.options, {count: 1}),
    error => error.stage === 'output validation' && error.repairCount === 1);
  assert.equal(fake.count(), 1);
});

test('a failed repair call stops and leaves the budget consumed', async () => {
  const fake = fakeClaude(1);
  const state = {count: 0};
  let calls = 0;
  const fetchImpl = async (url, options) => {
    calls++;
    if (calls === 2) return {ok: false, status: 503};
    return fake.options.fetchImpl(url, options);
  };
  await assert.rejects(runTutor(context, {...fake.options, fetchImpl}, state),
    error => {
      assert.equal(error.stage, 'model call');
      assert.equal(error.repairCount, 1);
      assert.deepEqual(error.usage, {input_tokens: 100, output_tokens: 50});
      return true;
    });
  assert.equal(calls, 2);
  assert.equal(state.count, 1);
});

test('repair receives original material and a correction request, not raw output', async () => {
  const fake = fakeClaude(1);
  let calls = 0;
  const fetchImpl = async (url, options) => {
    calls++;
    const input = JSON.parse(JSON.parse(options.body).messages[0].content);
    if (calls === 2) {
      assert.ok(input.repair_request.reason.includes('concept'));
      assert.equal(input.controller_context.handoff_id, context.handoffId);
      assert.equal(input.approved_lesson.concepts.length, 7);
      assert.equal(input.controller_context.round, 'first');
      assert.ok(!options.body.includes('c_invented'));
    }
    return fake.options.fetchImpl(url, options);
  };
  await runTutor(context, {...fake.options, fetchImpl});
  assert.equal(calls, 2);
});

test('invalid repair budget fails before generation', async () => {
  const fake = fakeClaude();
  await assert.rejects(runTutor(context, fake.options, {count: 2}), /repair budget validation/);
  assert.equal(fake.count(), 0);
});


test('adds actual per-call token counts, including cache usage', async () => {
  const fake = fakeClaude(1);
  let calls = 0;
  const fetchImpl = async (url, options) => {
    const response = await fake.options.fetchImpl(url, options);
    const body = await response.json();
    body.usage = calls++ === 0
      ? {input_tokens: 100, output_tokens: 50, cache_read_input_tokens: 20}
      : {input_tokens: 160, output_tokens: 70, cache_read_input_tokens: 30};
    return {ok: true, json: async () => body};
  };
  const result = await runTutor(context, {...fake.options, fetchImpl});
  assert.deepEqual(result.usage, {
    input_tokens: 260, output_tokens: 120, cache_read_input_tokens: 50,
  });
});

test('controller must authorize repair before the second provider request', async () => {
  const fake = fakeClaude(1); let reservations = 0;
  const state = {count:0, reserve:async()=>{assert.equal(fake.count(),1); reservations++; return true;}};
  const result = await runTutor(context,fake.options,state);
  assert.equal(reservations,1); assert.equal(fake.count(),2); assert.equal(result.repairCount,1);
});
test('denied or failed durable reservation prevents the repair provider call', async () => {
  for (const reserve of [async()=>false, async()=>{throw new Error('database unavailable');}]) {
    const fake = fakeClaude(1);
    await assert.rejects(runTutor(context,fake.options,{count:0,reserve}),TutorRunError);
    assert.equal(fake.count(),1);
  }
});
