import { buildTutorInput } from './tutor-input.mjs';
import { callClaude } from './claude.mjs';
import { validateTutorOutput } from './tutor-output.mjs';

export class TutorRunError extends Error {
  constructor(stage, repairCount = 0, usage = {}) {
    super(`Tutor failed during ${stage}.`);
    this.name = 'TutorRunError';
    this.stage = stage;
    this.repairCount = repairCount;
    this.usage = {...usage};
  }
}

// This is technical repair, NOT the student's reteaching/quiz retry.
// The caller owns this state per handoff. It is in-memory for now; the
// controller must persist/reserve it before allowing repairs across restarts.
export async function runTutor(context, claudeOptions = {}, repairState = {count: 0}) {
  if (!repairState || ![0, 1].includes(repairState.count)) {
    throw new TutorRunError('repair budget validation');
  }
  let stage = 'input preparation';
  const usage = {};
  try {
    const originalInput = await buildTutorInput(context);
    let nextInput = originalInput;

    // At most two generations: the original, then one correction if allowed.
    for (let generation = 0; generation < 2; generation++) {
      stage = 'model call';
      const response = await callClaude(nextInput, claudeOptions);
      // Count every completed response, including an invalid handoff that
      // consumed tokens. Missing provider usage stays unknown, not estimated.
      for (const [key, value] of Object.entries(response.usage ?? {})) {
        if (key.endsWith('_tokens') && Number.isFinite(value) && value >= 0) {
          usage[key] = (usage[key] ?? 0) + value;
        }
      }

      stage = 'output validation';
      try {
        const handoff = validateTutorOutput(response.text, originalInput);
        return {handoff, model: response.model, usage: {...usage}, repairCount: repairState.count};
      } catch (error) {
        if (repairState.count === 1) throw error;

        // Reserve the only repair before calling Claude again. If the call
        // fails, that reservation remains consumed; there is no third call.
        repairState.count = 1;
        // Do not echo arbitrary model text or schema paths into instructions.
        const reason = error.message.startsWith('Tutor handoff does not match v1 schema:')
          ? 'The response did not match the supplied v1 JSON Schema.'
          : error.message;
        nextInput = {
          system: originalInput.system,
          user: JSON.stringify({
            ...JSON.parse(originalInput.user),
            repair_request: {
              reason,
              instruction: 'This is the single technical repair attempt. Regenerate a complete handoff using the original approved lesson, IDs, sources, and schema. Do not invent missing lesson content.',
            },
          }),
        };
      }
    }
    throw new TutorRunError('output validation', repairState.count, usage);
  } catch {
    throw new TutorRunError(stage, repairState.count, usage);
  }
}
