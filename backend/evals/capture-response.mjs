// Evaluation-only Node preload. Never loaded by normal application code.
import {appendFile} from 'node:fs/promises';

const path = process.env.STUDY_BUDDY_EVAL_CAPTURE_PATH;
if (!path) throw new Error('Evaluation capture requires an explicit destination.');
const originalFetch = globalThis.fetch;
globalThis.fetch = async (...args) => {
  const response = await originalFetch(...args);
  // Do not read error bodies, headers, request arguments, or private reasoning.
  if (response.ok) {
    const body = await response.clone().json();
    const usage = {};
    for (const field of ['input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens']) {
      const value = body.usage?.[field];
      if (Number.isSafeInteger(value) && value >= 0) usage[field] = value;
    }
    const thinking = body.usage?.output_tokens_details?.thinking_tokens;
    if (Number.isSafeInteger(thinking) && thinking >= 0) usage.thinking_tokens = thinking;
    const record = {usage,
      text: Array.isArray(body.content) ? body.content.filter(b => b.type === 'text' && typeof b.text === 'string').map(b => b.text).join('') : '',
      stop_reason: ['end_turn', 'max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal'].includes(body.stop_reason) ? body.stop_reason : 'unknown',
    };
    await appendFile(path, JSON.stringify(record) + '\n', {mode: 0o600});
  }
  return response;
};
