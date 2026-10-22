// Transport only: one request, no tool loop and no automatic repair/retry.
// The next harness step must validate the returned text before using it.
export async function callClaude(input, {
  apiKey = process.env.ANTHROPIC_API_KEY,
  model = process.env.ANTHROPIC_MODEL || 'claude-haiku-5-5',
  fetchImpl = globalThis.fetch,
  timeoutMs = 30_000,
} = {}) {
  if (typeof apiKey !== 'string' || !apiKey.trim()) {
    throw new Error('ANTHROPIC_API_KEY is required.');
  }
  if (!input?.system?.trim() || !input?.user?.trim()) {
    throw new Error('Tutor system instructions and user input are required.');
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0) {
    throw new Error('A positive request timeout is required.');
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
        max_tokens: 4096,
        system: input.system,
        messages: [{role: 'user', content: input.user}],
      }),
    });

    // Do not expose provider bodies: they may echo request data or credentials.
    if (!response.ok) throw new Error(`Claude request failed (HTTP ${response.status}).`);
    const result = await response.json();
    if (result.stop_reason !== 'end_turn') {
      throw new Error('Claude did not finish a complete response.');
    }
    if (!Array.isArray(result.content) || result.content.length === 0 ||
        result.content.some(block => !['text', 'thinking', 'redacted_thinking'].includes(block.type) ||
          (block.type === 'text' && typeof block.text !== 'string'))) {
      throw new Error('Claude returned an unexpected response format.');
    }
    // Thinking blocks are provider metadata, never teaching content or handoffs.
    const text = result.content.filter(block => block.type === 'text').map(block => block.text).join('');
    if (!text.trim()) throw new Error('Claude returned an empty response.');
    return {text, model: result.model, usage: result.usage};
  } catch (error) {
    if (controller.signal.aborted) throw new Error('Claude request timed out.');
    // Only relay our fixed messages, never raw transport exceptions.
    const safe = [
      /^Claude request failed \(HTTP \d+\)\.$/,
      /^Claude did not finish a complete response\.$/,
      /^Claude returned an (unexpected response format|empty response)\.$/,
    ];
    if (safe.some(pattern => pattern.test(error.message))) throw error;
    throw new Error('Could not reach Claude or read its response.');
  } finally {
    clearTimeout(timer);
  }
}
