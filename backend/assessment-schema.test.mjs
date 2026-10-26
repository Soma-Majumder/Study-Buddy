import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import Ajv2020 from 'ajv/dist/2020.js';
import {assessmentResponseSchema} from './assessment-schema.mjs';

const validate = new Ajv2020({allErrors: true}).compile(assessmentResponseSchema);
const fixture = async () => JSON.parse(await readFile(new URL('../schemas/v1/examples/quiz_output.example.json', import.meta.url), 'utf8'));

test('provider schema accepts quiz and explicit insufficient-content branch', async () => {
  assert.equal(validate({result: await fixture()}), true);
  assert.equal(validate({result: {error: {code: 'insufficient_quiz_content', message: 'Not enough teaching.'}}}), true);
  assert.equal(validate({result: {error: {code: 'other', message: 'unsupported'}}}), false);
});

test('grammar schema rejects the observed extra fields and mixed quiz/error output', async () => {
  for (const mutate of [q => {q.answer_key[0].concept_id = 'c_extra';},
    q => {q.quiz.answer_key_placeholder = [];}, q => {q.error = {code: 'insufficient_quiz_content', message: 'mixed'};}]) {
    const q = await fixture(); mutate(q);
    assert.equal(validate({result: q}), false);
  }
});

test('provider projection omits unsupported bounds without changing canonical validation', async () => {
  const success = assessmentResponseSchema.properties.result.anyOf[0];
  assert.equal(success.properties.quiz.properties.questions.minItems, undefined);
  assert.equal(success.properties.quiz.properties.questions.maxItems, undefined);
  assert.equal(success.properties.answer_key.items.properties.position.minimum, undefined);
  assert.equal(assessmentResponseSchema.$defs.id.minLength, undefined);
  const canonical = JSON.parse(await readFile(new URL('../schemas/v1/quiz_output.schema.json', import.meta.url), 'utf8'));
  assert.equal(canonical.properties.quiz.properties.questions.minItems, 10);
  const full = new Ajv2020().compile(canonical);
  const q = await fixture(); q.quiz.questions.pop();
  assert.equal(validate({result: q}), true); // Size stays the local validator's responsibility.
  assert.equal(full(q), false);
});
