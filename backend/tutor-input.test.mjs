import test from 'node:test';
import assert from 'node:assert/strict';
import { buildTutorInput } from './tutor-input.mjs';
import { QUIZ_EXPECTED_OUTPUT } from './handoff-policy.mjs';

const context = {
  lessonId: 'lesson_cell_membranes',
  sessionId: 'session_q8w1e5r2t6y4',
  handoffId: 'handoff_a1b2c3d4e5f6',
  explanationId: 'expl_z9y8x7w6v5u4',
};

test('first-round input keeps instructions separate and preserves controller IDs and approved content', async () => {
  const {system, user} = await buildTutorInput(context);
  const input = JSON.parse(user);
  assert.match(system, /Tutor/);
  assert.equal(input.controller_context.learner_session_id, context.sessionId);
  assert.equal(input.controller_context.handoff_id, context.handoffId);
  assert.equal(input.controller_context.explanation_id, context.explanationId);
  assert.equal(input.controller_context.round, 'first');
  assert.equal(input.approved_lesson.concepts.length, 7);
  assert.match(input.approved_lesson.source_text, /Saturated chains lack carbon/);
  assert.match(input.approved_lesson.source_text, /glycocalyx/);
  assert.equal(input.sources[0].lesson_id, context.lessonId);
  assert.equal(input.sources[0].lesson_version, input.approved_lesson.version);
  assert.equal(input.sources[0].license, 'CC BY-NC-SA 4.0');
  assert.equal(input.output_schema.properties.task.const, 'create_quiz');
  assert.equal(input.expected_output, QUIZ_EXPECTED_OUTPUT);
  assert.deepEqual(Object.keys(input).sort(), ['approved_lesson', 'controller_context', 'expected_output', 'output_schema', 'sources']);
});

test('rejects absent or wrong-kind controller IDs before building a request', async () => {
  await assert.rejects(buildTutorInput({...context, handoffId: undefined}), /controller-supplied handoff ID/);
  await assert.rejects(buildTutorInput({...context, sessionId: context.explanationId}), /controller-supplied session ID/);
});

test('missing lessons cannot become model inputs', async () => {
  await assert.rejects(buildTutorInput({...context, lessonId: 'lesson_missing'}), /Lesson not found/);
});
