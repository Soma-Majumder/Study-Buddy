import { buildAssessmentInput } from './assessment-input.mjs';
import { callClaude, ClaudeCallError } from './claude.mjs';

export class AssessmentRunError extends Error {
  constructor(stage, diagnostic = {}) {
    super(`Assessment failed during ${stage}.`);
    this.name = 'AssessmentRunError';
    this.stage = stage;
    this.code = diagnostic.code || (stage === 'input preparation' ? 'invalid_input' : 'adapter_error');
    if (diagnostic.httpStatus !== undefined) this.httpStatus = diagnostic.httpStatus;
    if (diagnostic.stopReason !== undefined) this.stopReason = diagnostic.stopReason;
  }
}

// One generation per invocation. Only the trusted controller may request repair.
// Never call this directly from learner input. Python owns quiz validation,
// persisted repair reservations, private-key storage, and learning retries.
export async function runAssessmentQuiz(handoff, {repairRequest = null, claudeOptions = {}} = {}) {
  let stage = 'input preparation';
  try {
    if (repairRequest !== null && (typeof repairRequest !== 'string' || !repairRequest.trim())) {
      throw new Error('Invalid controller correction request.');
    }
    const input = buildAssessmentInput(handoff);
    if (repairRequest !== null) {
      // Use fixed correction instructions, never arbitrary caller/model prose.
      input.user = JSON.stringify({
        ...JSON.parse(input.user),
        repair_request: 'Regenerate a complete quiz matching the original IDs, round, taught concepts, and schema. Return exactly 10 questions with four distinct options and a private key entry per position. This is the controller-authorized technical repair. Do not invent missing teaching content.',
      });
    }
    stage = 'model call';
    const response = await callClaude(input, {maxTokens: 8192, timeoutMs: 60_000, effort: 'low', ...claudeOptions});
    // Return invalid output to controller validation so its existing repair
    // policy applies. Never retry locally or expose raw non-JSON model text.
    let output = null;
    try { output = JSON.parse(response.text); } catch { /* invalid generation */ }
    return {output, model: response.model, usage: response.usage};
  } catch (error) {
    // Provider errors and malformed input stop; do not leak provider bodies.
    throw new AssessmentRunError(stage, error instanceof ClaudeCallError ? error : {});
  }
}
