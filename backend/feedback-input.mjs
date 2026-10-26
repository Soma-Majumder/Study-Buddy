import {readFile} from 'node:fs/promises';

const read = async path => readFile(new URL(path, import.meta.url), 'utf8');
const outputSchema = JSON.parse(await read('../schemas/v1/result_explanation.schema.json'));
const system = await read('./prompts/feedback-system.md');
const nonblank = value => typeof value === 'string' && value.trim().length > 0;
const reject = () => { throw new Error('Feedback requires an intact controller context.'); };

// Backend-only. Structure checks only: ownership and grading were verified by Python.
// The output schema is rebuilt here, never trusted from the caller.
export function buildFeedbackInput(context, repairRequest = null) {
  if (!context || typeof context !== 'object' || context.task !== 'explain_verified_results') reject();
  const {quiz_id, attempt_id, explanation_text, graded, sources} = context;
  if (!nonblank(quiz_id) || !nonblank(attempt_id) || !nonblank(explanation_text) || !graded ||
      !Array.isArray(graded.items) || graded.items.length !== 10 || !Array.isArray(graded.weak_concepts)) reject();
  const positions = graded.items.map(i => i.position).sort((a, b) => a - b);
  if (positions.some((p, index) => p !== index + 1)) reject();
  if (!Array.isArray(sources) || sources.length !== 1) reject();
  if (repairRequest !== null && !nonblank(repairRequest)) reject();
  const request = {
    task: 'explain_verified_results',
    quiz_id, attempt_id,
    round: context.round,
    explanation_text,
    results: graded.items.map(({position, concept_id, selected, correct_option, is_correct, rationale}) =>
      ({position, concept_id, selected, correct_option, is_correct, rationale})),
    weak_concepts: graded.weak_concepts,
    sources,
    output_schema: outputSchema,
  };
  // Fixed correction wording only, never arbitrary caller or model prose.
  if (repairRequest !== null) {
    request.repair_request = 'Return a complete result-explanation object for the original quiz and attempt. Provide ten nonempty feedback entries, one per question position, and exactly the verified weak concepts and missed counts. Do not add a score field or extra fields. This is the single repair.';
  }
  return {system, user: JSON.stringify(request)};
}
