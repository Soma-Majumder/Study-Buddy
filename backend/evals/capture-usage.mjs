// Evaluation-only preload: aggregate metadata, never prompt/answer/reasoning text.
import {appendFile} from 'node:fs/promises';

const path = process.env.STUDY_BUDDY_EVAL_CAPTURE_PATH;
if (!path) throw new Error('Usage capture requires an explicit private destination.');
const phases = new Set(['assessment_generation', 'assessment_repair', 'review', 'revision', 'review_revision']);
const originalFetch = globalThis.fetch;
globalThis.fetch = async (...args) => {
  const response = await originalFetch(...args);
  // Capture successful HTTP bodies before callClaude rejects max_tokens, while
  // leaving the original response untouched. HTTP errors may have no usage.
  if (response.ok) {
    const body = await response.clone().json();
    const usage = {};
    for (const key of ['input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens']) {
      const value = body.usage?.[key];
      if (Number.isSafeInteger(value) && value >= 0) usage[key] = value;
    }
    const thinking = body.usage?.output_tokens_details?.thinking_tokens;
    if (Number.isSafeInteger(thinking) && thinking >= 0) usage.thinking_tokens = thinking;
    const phase = process.env.STUDY_BUDDY_EVAL_PHASE;
    const textCharacters = Array.isArray(body.content) ? body.content.reduce((n, b) =>
      n + (b.type === 'text' && typeof b.text === 'string' ? b.text.length : 0), 0) : 0;
    await appendFile(path, JSON.stringify({phase: phases.has(phase) ? phase : 'unknown', usage,
      text_characters: textCharacters,
      stop_reason: ['end_turn', 'max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal'].includes(body.stop_reason) ? body.stop_reason : 'unknown',
    }) + '\n', {mode: 0o600});
  }
  return response;
};
