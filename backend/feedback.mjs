import {buildFeedbackInput} from './feedback-input.mjs';
import {providerProjection} from './provider-schema.mjs';
import {callClaude, ClaudeCallError} from './claude.mjs';
import {readFile} from 'node:fs/promises';

const canonical = JSON.parse(await readFile(new URL('../schemas/v1/result_explanation.schema.json', import.meta.url), 'utf8'));
const feedbackResponseSchema = providerProjection(canonical);

export class FeedbackRunError extends Error {
  constructor(stage, diagnostic = {}) {
    super(`Feedback failed during ${stage}.`);
    this.name = 'FeedbackRunError';
    this.stage = stage;
    this.code = diagnostic.code || (stage === 'input preparation' ? 'invalid_input' : 'adapter_error');
    if (diagnostic.httpStatus !== undefined) this.httpStatus = diagnostic.httpStatus;
    if (diagnostic.stopReason !== undefined) this.stopReason = diagnostic.stopReason;
  }
}

// One generation per invocation. Python owns validation, the single repair,
// and saved state. Invalid output is returned as null so the controller's
// repair policy applies; raw model text is never exposed.
export async function runFeedback(context, {repairRequest = null, claudeOptions = {}} = {}) {
  let stage = 'input preparation';
  try {
    const input = buildFeedbackInput(context, repairRequest);
    stage = 'model call';
    const response = await callClaude(input, {maxTokens: 6144, timeoutMs: 60_000, effort: 'low', ...claudeOptions,
                                              outputSchema: structuredClone(feedbackResponseSchema)});
    let output = null;
    try { output = JSON.parse(response.text); } catch { /* invalid generation */ }
    return {output, model: response.model, usage: response.usage};
  } catch (error) {
    throw new FeedbackRunError(stage, error instanceof ClaudeCallError ? error : {});
  }
}
