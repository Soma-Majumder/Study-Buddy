import {readFile} from 'node:fs/promises';
import {buildReviewerInput} from './reviewer-input.mjs';
import {matchesReviewerSchema} from './reviewer-schema.mjs';
import {assessmentResponseSchema} from './assessment-schema.mjs';
import {AssessmentRunError} from './assessment.mjs';
import {callClaude, ClaudeCallError} from './claude.mjs';

const system = await readFile(new URL('./prompts/assessment-revision-system.md', import.meta.url), 'utf8');

// Private advisory only: computed from the validated candidate, never a verdict.
export function optionLengthWarnings(candidate) {
  const keys = new Map(candidate.answer_key.map(k => [k.position, k.correct_option]));
  return candidate.quiz.questions.flatMap(q => {
    const correct = keys.get(q.position);
    const lengths = Object.entries(q.options).map(([letter, text]) => [letter, Array.from(text.trim()).length]);
    const meanOther = lengths.filter(([letter]) => letter !== correct).reduce((sum, [, n]) => sum + n, 0) / 3;
    const ratio = lengths.find(([letter]) => letter === correct)[1] / meanOther;
    return ratio > 1.3 ? [{position:q.position, code:'option_length_flag', ratio:Math.round(ratio * 1000) / 1000}] : [];
  });
}

export async function runAssessmentRevision(context, {technicalRepair = false, claudeOptions = {}} = {}) {
  let stage = 'input preparation';
  try {
    if (typeof technicalRepair !== 'boolean') throw new Error('Invalid correction flag.');
    const original = JSON.parse(buildReviewerInput(context?.original).user);
    delete original.output_schema;
    const report = context.review_report;
    if (original.controller_context.candidate_version !== 0 || !matchesReviewerSchema(report) ||
        Object.entries(original.controller_context).some(([key, value]) => report[key] !== value)) {
      throw new Error('Revision requires a bound review report.');
    }
    const input = {system, user: JSON.stringify({...original, review_report: report,
      advisory_checks: {option_length_warnings: optionLengthWarnings(original.candidate)},
      output_schema: JSON.parse(await readFile(new URL('../schemas/v1/quiz_output.schema.json', import.meta.url), 'utf8')),
      ...(technicalRepair ? {repair_request: 'Regenerate the complete content revision with valid quiz structure and unchanged IDs/scope. This consumes the existing shared technical repair; it grants no additional content revision.'} : {})})};
    stage = 'model call';
    const response = await callClaude(input, {maxTokens:8192, timeoutMs:60_000, effort:'low', fastStructuredOutput:true, ...claudeOptions,
      outputSchema: structuredClone(assessmentResponseSchema)});
    let output = null;
    try { output = JSON.parse(response.text)?.result ?? null; } catch { /* controller validates and owns repair */ }
    return {output, model:response.model, usage:response.usage};
  } catch (error) {
    throw new AssessmentRunError(stage, error instanceof ClaudeCallError ? error : {});
  }
}
