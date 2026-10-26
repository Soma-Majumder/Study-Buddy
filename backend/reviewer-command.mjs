// Backend-only stdin/stdout protocol. Reports can expose private answer choices.
import {runReviewer, ReviewerRunError} from './reviewer.mjs';

try {
  let text = '';
  for await (const chunk of process.stdin) text += chunk;
  const {context, provider_timeout_ms = 60_000} = JSON.parse(text);
  if (!Number.isInteger(provider_timeout_ms) || provider_timeout_ms <= 0 || provider_timeout_ms > 60_000) {
    throw new ReviewerRunError('input preparation', 'invalid_timeout');
  }
  const result = await runReviewer(context, {claudeOptions: {timeoutMs: provider_timeout_ms}});
  console.log(JSON.stringify({ok: true, result}));
} catch (error) {
  const known = error instanceof ReviewerRunError;
  console.log(JSON.stringify({ok: false, error: {
    stage: known ? error.stage : 'input preparation',
    code: known ? error.code : 'invalid_input',
    ...(known && error.httpStatus !== undefined ? {http_status: error.httpStatus} : {}),
    ...(known && error.stopReason !== undefined ? {stop_reason: error.stopReason} : {}),
  }}));
  process.exitCode = 1;
}
