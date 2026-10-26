import { readFile } from 'node:fs/promises';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { QUIZ_EXPECTED_OUTPUT } from './handoff-policy.mjs';

const readProject = path => readFile(new URL(path, import.meta.url), 'utf8');
const schema = JSON.parse(await readProject('../schemas/v1/tutor_to_assessment.schema.json'));
const ajv = new Ajv2020({allErrors: true});
addFormats(ajv);
const matchesSchema = ajv.compile(schema);
const system = await readProject('./prompts/assessment-quiz-system.md');
const outputSchema = JSON.parse(await readProject('../schemas/v1/quiz_output.schema.json'));

// Backend-only: caller must already verify the handoff against controller records.
// This boundary checks structure and scope, not ownership or factual accuracy.
// No provider calls, database writes, counters, or retries occur here.
export function buildAssessmentInput(handoff) {
  if (!matchesSchema(handoff)) throw new Error('Assessment requires a v1 Tutor handoff.');
  const p = handoff.payload;
  if (handoff.expected_output !== QUIZ_EXPECTED_OUTPUT) {
    throw new Error('Tutor changed the fixed quiz instructions.');
  }
  if (!p.explanation_text.trim() || p.concepts.some(c => !c.name.trim() || !c.taught_summary.trim())) {
    throw new Error('Assessment requires nonempty teaching content.');
  }
  const taught = new Set(p.concepts.map(c => c.concept_id));
  if (taught.size !== p.concepts.length) throw new Error('Taught concepts must be unique.');
  if (handoff.sources.some(s => s.lesson_id !== p.lesson_id || s.lesson_version !== p.lesson_version)) {
    throw new Error('Source references must match the handoff lesson version.');
  }
  if (handoff.context.round === 'first' && p.focus_concept_ids !== undefined) {
    throw new Error('First-round handoffs cannot contain retry focus.');
  }
  if (handoff.context.round === 'retry') {
    const focus = p.focus_concept_ids;
    if (!Array.isArray(focus) || focus.length !== taught.size || new Set(focus).size !== focus.length ||
        focus.some(id => !taught.has(id))) {
      throw new Error('Retry focus must match exactly the taught concepts.');
    }
  }
  // Serialize immediately: later callback mutations cannot change the request.
  // Agent-authored expected_output and uncertainty are not instruction channels.
  const user = JSON.stringify({
    handoff: {
      schema_version: handoff.schema_version,
      handoff_id: handoff.handoff_id,
      context: handoff.context,
      sources: handoff.sources,
      payload: p,
    },
    output_schema: outputSchema,
  });
  return {system, user};
}
