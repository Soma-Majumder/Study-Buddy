// Backend-only stdin/stdout bridge. Stdout is one JSON envelope, never provider bodies.
import {runFeedback, FeedbackRunError} from './feedback.mjs';

try {
  let text = '';
  for await (const chunk of process.stdin) text += chunk;
  const {context, repair_request = null, provider_timeout_ms = 60_000} = JSON.parse(text);
  if (!Number.isInteger(provider_timeout_ms) || provider_timeout_ms <= 0 || provider_timeout_ms > 60_000) throw new Error('Invalid command input.');
  const result = await runFeedback(context, {repairRequest: repair_request, claudeOptions: {timeoutMs: provider_timeout_ms}});
  console.log(JSON.stringify({ok: true, result}));
} catch (error) {
  console.log(JSON.stringify({ok: false, error: {
    stage: error instanceof FeedbackRunError ? error.stage : 'input preparation',
    code: error instanceof FeedbackRunError ? error.code : 'invalid_input',
    ...(error instanceof FeedbackRunError && error.httpStatus !== undefined ? {http_status: error.httpStatus} : {}),
    ...(error instanceof FeedbackRunError && error.stopReason !== undefined ? {stop_reason: error.stopReason} : {}),
  }}));
  process.exitCode = 1;
}
