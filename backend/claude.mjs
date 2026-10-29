// Fixed diagnostic codes only; never attach provider bodies, headers, or causes.
export class ClaudeCallError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'ClaudeCallError';
    this.code = code;
    if (Number.isInteger(details.httpStatus) && details.httpStatus >= 100 && details.httpStatus <= 599) {
      this.httpStatus = details.httpStatus;
    }
    if (['max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal', 'unknown'].includes(details.stopReason)) {
      this.stopReason = details.stopReason;
    }
  }
}

// Transport only: one request, no tool loop and no automatic repair/retry.
// The next harness step must validate the returned text before using it.
export async function callClaude(input, {
  apiKey = process.env.ANTHROPIC_API_KEY,
  model = process.env.ANTHROPIC_MODEL || 'claude-haiku-5-5',
  fetchImpl = globalThis.fetch,
  timeoutMs = 30_000,
  maxTokens = 4096,
  effort,
  outputSchema,
  fastStructuredOutput = false,
  allowPaidCalls = process.env.STUDY_BUDDY_ALLOW_PAID_CLAUDE === 'true',
} = {}) {
  if (typeof apiKey !== 'string' || !apiKey.trim()) {
    throw new ClaudeCallError('missing_api_key', 'ANTHROPIC_API_KEY is required.');
  }
  if (!input?.system?.trim() || !input?.user?.trim()) {
    throw new ClaudeCallError('invalid_input', 'Tutor system instructions and user input are required.');
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0) {
    throw new ClaudeCallError('invalid_timeout', 'A positive request timeout is required.');
  }

  if (!Number.isInteger(maxTokens) || maxTokens <= 0) {
    throw new ClaudeCallError('invalid_output_budget', 'A positive output token budget is required.');
  }

  if (effort !== undefined && !['low', 'medium', 'high', 'xhigh', 'max'].includes(effort)) {
    throw new ClaudeCallError('invalid_effort', 'A supported reasoning effort is required.');
  }

  if (outputSchema !== undefined && (!outputSchema || typeof outputSchema !== 'object' || Array.isArray(outputSchema))) {
    throw new ClaudeCallError('invalid_output_schema', 'An output schema object is required.');
  }

  if (typeof fastStructuredOutput !== 'boolean') {
    throw new ClaudeCallError('invalid_input', 'Structured-output mode must be a trusted boolean.');
  }

  // A credential or custom transport is not consent to spend API credits.
  // Only trusted backend configuration may grant permission, never model/user data.
  if (allowPaidCalls !== true) {
    throw new ClaudeCallError('live_calls_disabled',
      'Paid Claude calls are disabled. Enable them explicitly in trusted backend configuration.');
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
      },
      signal: controller.signal,
      body: JSON.stringify({
        model,
        max_tokens: maxTokens,
        // Haiku 5.5 defaults to hidden adaptive thinking, which shares max_tokens.
        // These are isolated JSON-only calls without tools. Keep other models'
        // thinking defaults; disabled is not supported universally.
        ...(fastStructuredOutput && /^claude-haiku-5-5(?:-|$)/.test(model) &&
            ['low', 'medium', 'high'].includes(effort) ? {thinking: {type: 'disabled'}} : {}),
        ...((effort !== undefined || outputSchema !== undefined) ? {output_config: {
          ...(effort !== undefined ? {effort} : {}),
          ...(outputSchema !== undefined ? {format: {type: 'json_schema', schema: outputSchema}} : {}),
        }} : {}),
        system: input.system,
        messages: [{role: 'user', content: input.user}],
      }),
    });

    // Do not expose provider bodies: they may echo request data or credentials.
    if (!response.ok) {
      const status = response.status;
      const code = [401, 403].includes(status) ? 'authentication_failed'
        : status === 429 ? 'rate_limited' : status >= 500 ? 'provider_unavailable' : 'api_rejected';
      throw new ClaudeCallError(code, `Claude request failed (HTTP ${status}).`, {httpStatus: status});
    }
    const result = await response.json();
    if (result.stop_reason !== 'end_turn') {
      const reason = ['max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal'].includes(result.stop_reason)
        ? result.stop_reason : 'unknown';
      throw new ClaudeCallError(reason === 'max_tokens' ? 'output_truncated' : 'incomplete_response',
        'Claude did not finish a complete response.', {stopReason: reason});
    }
    if (!Array.isArray(result.content) || result.content.length === 0 ||
        result.content.some(block => !['text', 'thinking', 'redacted_thinking'].includes(block.type) ||
          (block.type === 'text' && typeof block.text !== 'string'))) {
      throw new ClaudeCallError('unexpected_response', 'Claude returned an unexpected response format.');
    }
    // Thinking blocks are provider metadata, never teaching content or handoffs.
    const text = result.content.filter(block => block.type === 'text').map(block => block.text).join('');
    if (!text.trim()) throw new ClaudeCallError('empty_response', 'Claude returned an empty response.');
    return {text, model: result.model, usage: result.usage};
  } catch (error) {
    if (controller.signal.aborted) throw new ClaudeCallError('request_timeout', 'Claude request timed out.');
    if (error instanceof ClaudeCallError) throw error;
    throw new ClaudeCallError('transport_error', 'Could not reach Claude or read its response.');
  } finally {
    clearTimeout(timer);
  }
}
