// Explicit live-only evaluation: one paid generation, no automatic repair.
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {runAssessmentQuiz} from '../assessment.mjs';
import {buildAssessmentInput} from '../assessment-input.mjs';

if (!process.argv.includes('--live')) {
  throw new Error('Use --live explicitly to authorize one paid Assessment generation.');
}
const handoff = JSON.parse(await readFile(new URL('../../schemas/v1/examples/tutor_to_assessment.example.json', import.meta.url), 'utf8'));
const input = buildAssessmentInput(handoff);
const hash = text => createHash('sha256').update(text).digest('hex');
const report = {date: new Date().toISOString(), scope: 'One first-round Assessment quiz from the hand-written Tutor fixture',
  prompt_sha256: hash(input.system), input_sha256: hash(input.user), handoff,
  human_review: 'pending', repairs: 0};
let calls = 0;
const started = performance.now();
try {
  const result = await runAssessmentQuiz(handoff, {claudeOptions: {allowPaidCalls: true, fetchImpl: async (...args) => {
    calls++;
    return fetch(...args);
  }}});
  report.output = result.output;
  report.model = result.model;
  report.usage = result.usage;
} catch (error) {
  report.error_stage = error.stage || 'request';
  report.error_code = error.code || 'adapter_error';
  if (error.httpStatus !== undefined) report.http_status = error.httpStatus;
  if (error.stopReason !== undefined) report.stop_reason = error.stopReason;
}
report.calls = calls;
report.latency_ms = Math.round(performance.now() - started);
const folder = new URL('./results/', import.meta.url);
await mkdir(folder, {recursive: true});
const path = new URL(`${report.date.replace(/[:.]/g, '-')}-assessment-quiz.json`, folder);
await writeFile(path, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({report: path.pathname, calls, error_stage: report.error_stage || null}));
