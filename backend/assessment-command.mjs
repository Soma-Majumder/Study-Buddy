// Backend-only stdin/stdout bridge. Its output can include private quiz keys.
import { runAssessmentQuiz, AssessmentRunError } from './assessment.mjs';
import {runAssessmentRevision} from './assessment-revision.mjs';

try {
  let text = '';
  for await (const chunk of process.stdin) text += chunk;
  const {handoff, repair_request = null, revision_context, technical_repair = false, provider_timeout_ms = 60_000} = JSON.parse(text);
  if (!Number.isInteger(provider_timeout_ms) || provider_timeout_ms <= 0 || provider_timeout_ms > 60_000 || (revision_context !== undefined && handoff !== undefined)) throw new Error('Invalid command input.');
  const result = revision_context !== undefined
    ? await runAssessmentRevision(revision_context, {technicalRepair: technical_repair, claudeOptions: {timeoutMs: provider_timeout_ms}})
    : await runAssessmentQuiz(handoff, {repairRequest: repair_request, claudeOptions: {timeoutMs: provider_timeout_ms}});
  console.log(JSON.stringify({ok: true, result}));
} catch (error) {
  console.log(JSON.stringify({ok: false, error: {
    stage: error instanceof AssessmentRunError ? error.stage : 'input preparation',
    code: error instanceof AssessmentRunError ? error.code : 'invalid_input',
    ...(error instanceof AssessmentRunError && error.httpStatus !== undefined ? {http_status: error.httpStatus} : {}),
    ...(error instanceof AssessmentRunError && error.stopReason !== undefined ? {stop_reason: error.stopReason} : {}),
  }}));
  process.exitCode = 1;
}
