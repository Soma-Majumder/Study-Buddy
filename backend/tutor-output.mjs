import { readFile } from 'node:fs/promises';
import { isDeepStrictEqual } from 'node:util';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { QUIZ_EXPECTED_OUTPUT } from './handoff-policy.mjs';

const schema = JSON.parse(await readFile(new URL('../schemas/v1/tutor_to_assessment.schema.json', import.meta.url), 'utf8'));
const ajv = new Ajv2020({allErrors: true});
addFormats(ajv);
const matchesSchema = ajv.compile(schema);

// request must be the original harness-built input, never a model-supplied copy.
export function validateTutorOutput(text, request) {
  let handoff;
  try { handoff = JSON.parse(text); }
  catch { throw new Error('Tutor response is not valid JSON.'); }

  // Do not forward a local failure response as if it were a valid handoff.
  if (handoff?.error) throw new Error('Tutor reported insufficient lesson content.');
  if (!matchesSchema(handoff)) {
    const errors = matchesSchema.errors.map(e => `${e.instancePath || '/'}: ${e.keyword}`).join('; ');
    throw new Error(`Tutor handoff does not match v1 schema: ${errors}`);
  }

  const input = JSON.parse(request.user);
  const expected = input.controller_context;
  const lesson = input.approved_lesson;
  const payload = handoff.payload;
  if (handoff.handoff_id !== expected.handoff_id ||
      handoff.context.learner_session_id !== expected.learner_session_id ||
      handoff.context.round !== expected.round ||
      payload.explanation_id !== expected.explanation_id) {
    throw new Error('Tutor changed controller-owned IDs or round.');
  }
  if (payload.lesson_id !== lesson.lesson_id || payload.lesson_version !== lesson.version ||
      !isDeepStrictEqual(handoff.sources, input.sources)) {
    throw new Error('Tutor changed the approved lesson version or source metadata.');
  }

  const approved = new Map(lesson.concepts.map(c => [c.concept_id, c]));
  const seen = new Set();
  for (const concept of payload.concepts) {
    if (!approved.has(concept.concept_id) || seen.has(concept.concept_id) ||
        concept.name !== approved.get(concept.concept_id).name || !concept.taught_summary.trim()) {
      throw new Error('Tutor returned an unknown, duplicate, renamed, or empty concept.');
    }
    seen.add(concept.concept_id);
  }
  if (!payload.explanation_text.trim() || !handoff.expected_output.trim()) {
    throw new Error('Tutor returned empty teaching content or output instructions.');
  }
  if (handoff.expected_output !== QUIZ_EXPECTED_OUTPUT) {
    throw new Error('Tutor changed harness-owned quiz instructions.');
  }
  if (expected.round === 'first' && payload.focus_concept_ids !== undefined) {
    throw new Error('First-round output must not contain retry focus concepts.');
  }

  if (expected.round === 'retry') {
    const focus = payload.focus_concept_ids;
    const expectedFocus = new Set(input.focus_concept_ids);
    if (!Array.isArray(focus) || focus.length !== expectedFocus.size ||
        new Set(focus).size !== focus.length || focus.some(id => !expectedFocus.has(id)) ||
        seen.size !== expectedFocus.size || [...seen].some(id => !expectedFocus.has(id))) {
      throw new Error('Retry output must teach exactly the verified focus concepts.');
    }
  }

  // This is structural and reference validation, not a claim of factual accuracy.
  return handoff;
}
