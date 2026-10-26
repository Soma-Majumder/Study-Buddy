import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {buildTutorInput} from './tutor-input.mjs';
import {validateTutorOutput} from './tutor-output.mjs';
import {runTutor} from './tutor.mjs';

const json = async file => JSON.parse(await readFile(new URL(file, import.meta.url), 'utf8'));
const back = await json('../schemas/v1/examples/assessment_to_tutor.example.json');
back.context.learner_session_id = 'session_q8w1e5r2t6y4';
const base = {
  lessonId: 'lesson_cell_membranes', sessionId: 'session_q8w1e5r2t6y4',
  handoffId: 'handoff_a1b2c3d4e5f6', explanationId: 'expl_z9y8x7w6v5u4',
  round: 'retry', learningRetryCount: 0, returnHandoff: back,
  currentAttempt: {
    sessionId: 'session_q8w1e5r2t6y4', lessonId: 'lesson_cell_membranes', lessonVersion: 2,
    round: 'first', quizId: 'quiz_0001', attemptId: 'attempt_0001', score: 6,
    missedConceptCounts: {c_fatty_acid_saturation: 2, c_cholesterol_buffer: 2},
  },
};

test('reteaching input includes only verified weak concepts and relevant feedback', async () => {
  const input = JSON.parse((await buildTutorInput(base)).user);
  assert.equal(input.controller_context.round, 'retry');
  assert.match(input.approved_lesson.source_text, /Cholesterol buffers fluidity/);
  assert.ok(!input.approved_lesson.source_text.includes('glycocalyx'));
  assert.ok(!input.approved_lesson.source_text.includes('fluid mosaic'));
  assert.deepEqual(input.focus_concept_ids, ['c_fatty_acid_saturation', 'c_cholesterol_buffer']);
  assert.deepEqual(input.approved_lesson.concepts.map(c => c.concept_id), input.focus_concept_ids);
  assert.equal(input.feedback[0].note, 'Confused which type of tail causes kinks.');
});

test('invalid return handoffs stop before any generation', async () => {
  const changes = [
    c => {c.returnHandoff.payload.weak_concepts = [];},
    c => {c.returnHandoff.context.learner_session_id = 'session_other';},
    c => {c.returnHandoff.payload.attempt_id = 'attempt_old';},
    c => {c.returnHandoff.payload.lesson_version = 3;},
    c => {c.returnHandoff.sources[0].url = 'https://example.com';},
    c => {c.returnHandoff.payload.weak_concepts[0].concept_id = 'c_unknown';},
    c => {c.returnHandoff.payload.weak_concepts[0].missed_count = 1;},
    c => {c.returnHandoff.payload.weak_concepts.pop();},
    c => {c.returnHandoff.payload.weak_concepts.push(c.returnHandoff.payload.weak_concepts[0]);},
  ];
  for (const change of changes) {
    const c = structuredClone(base); change(c);
    await assert.rejects(runTutor(c, {allowPaidCalls: true, apiKey: 'fake', fetchImpl: () => assert.fail('No generation expected')}),
      error => error.stage === 'input preparation');
  }
});

test('8/10, consumed retry, or retry-round attempts cannot start reteaching', async () => {
  for (const change of [
    c => {c.currentAttempt.score = c.returnHandoff.payload.score = 8;},
    c => {c.learningRetryCount = 1;},
    c => {c.currentAttempt.round = 'retry';},
  ]) {
    const c = structuredClone(base); change(c);
    await assert.rejects(buildTutorInput(c), /retry is unavailable|below 8/);
  }
});

test('retry output must teach exactly the focus concepts, with a new supplied explanation ID', async () => {
  const input = await buildTutorInput(base);
  const h = await json('../schemas/v1/examples/tutor_to_assessment.retry.example.json');
  h.context.learner_session_id = base.sessionId;
  h.handoff_id = base.handoffId;
  h.payload.explanation_id = base.explanationId;
  assert.deepEqual(validateTutorOutput(JSON.stringify(h), input), h);
  for (const change of [
    x => {delete x.payload.focus_concept_ids;},
    x => {x.payload.focus_concept_ids.push(x.payload.focus_concept_ids[0]);},
    x => {x.payload.concepts.pop();},
    x => {x.payload.concepts[0].concept_id = 'c_glycocalyx_recognition';},
  ]) {
    const invalid = structuredClone(h); change(invalid);
    assert.throws(() => validateTutorOutput(JSON.stringify(invalid), input), /concept/);
  }
});

test('accepts a matching trusted controller reservation after the count is consumed', async () => {
  const c = structuredClone(base);
  c.learningRetryCount = 1;
  c.retryReservation = {status: 'dispatched', sessionId: c.sessionId,
    sourceAttemptId: c.currentAttempt.attemptId, handoffId: c.handoffId, explanationId: c.explanationId};
  const input = JSON.parse((await buildTutorInput(c)).user);
  assert.deepEqual(input.focus_concept_ids, ['c_fatty_acid_saturation', 'c_cholesterol_buffer']);
  assert.equal(input.retryReservation, undefined);
  for (const change of [
    x => {x.retryReservation.status = 'reserved';},
    x => {x.retryReservation.sessionId = 'session_other';},
    x => {x.retryReservation.sourceAttemptId = 'attempt_old';},
    x => {x.retryReservation.handoffId = 'handoff_other';},
    x => {x.retryReservation.explanationId = 'expl_other';},
    x => {x.learningRetryCount = 0;},
    x => {x.currentAttempt.score = x.returnHandoff.payload.score = 8;},
  ]) {
    const bad = structuredClone(c); change(bad);
    await assert.rejects(buildTutorInput(bad), /retry is unavailable|below 8/);
  }
});
