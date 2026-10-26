import {buildReviewerInput} from './reviewer-input.mjs';
import {matchesReviewerSchema, reviewerResponseSchema} from './reviewer-schema.mjs';
import {callClaude, ClaudeCallError} from './claude.mjs';

export class ReviewerRunError extends Error {
  constructor(stage, code, diagnostic = {}) {
    super(`Reviewer failed during ${stage}.`);
    this.name = 'ReviewerRunError';
    this.stage = stage;
    this.code = code;
    if (diagnostic.httpStatus !== undefined) this.httpStatus = diagnostic.httpStatus;
    if (diagnostic.stopReason !== undefined) this.stopReason = diagnostic.stopReason;
  }
}

// Trusted backend only. One invocation makes at most one call, with no local
// repair or retry. The future dispatcher owns the reservation and remaining
// operation deadline; Python validates evidence and stores the derived verdict.
export async function runReviewer(context, {claudeOptions = {}} = {}) {
  let stage = 'input preparation';
  try {
    const input = buildReviewerInput(context);
    // Compare with the serialized request, not a caller object mutable during fetch.
    const expected = JSON.parse(input.user);
    stage = 'model call';
    const response = await callClaude(input, {
      maxTokens: 4096, timeoutMs: 60_000, effort: 'low', ...claudeOptions,
      outputSchema: structuredClone(reviewerResponseSchema),
    });
    stage = 'response validation';
    let output;
    try { output = JSON.parse(response.text); }
    catch { throw new ReviewerRunError(stage, 'invalid_json'); }
    if (!matchesReviewerSchema(output)) throw new ReviewerRunError(stage, 'invalid_report');
    if (Object.entries(expected.controller_context).some(([key, value]) => output[key] !== value)) {
      throw new ReviewerRunError(stage, 'mismatched_report');
    }
    const questions = new Map(expected.candidate.quiz.questions.map(q => [q.position, q.concept_id]));
    if (new Set(output.questions.map(q => q.position)).size !== 10 ||
        output.questions.some(q => questions.get(q.position) !== q.concept_id)) {
      throw new ReviewerRunError(stage, 'mismatched_report');
    }
    // No model-supplied approval. Literal evidence, saved keys, uncertainty, and
    // policy decisions must still pass Python validate_review/store_review_report.
    return {output, model: response.model, usage: response.usage};
  } catch (error) {
    if (error instanceof ReviewerRunError) throw error;
    throw new ReviewerRunError(stage, error instanceof ClaudeCallError ? error.code :
      stage === 'input preparation' ? 'invalid_input' : 'adapter_error',
      error instanceof ClaudeCallError ? error : {});
  }
}
