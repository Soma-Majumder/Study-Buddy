// Backend adapter only. Send context through stdin; the real key stays in env.
// Stdout is one JSON envelope, never provider bodies or private reasoning.
import {runTutor, TutorRunError} from './tutor.mjs';

try {
  let text = '';
  for await (const chunk of process.stdin) text += chunk;
  const context = JSON.parse(text);
  const result = await runTutor(context);
  console.log(JSON.stringify({ok: true, result}));
} catch (error) {
  console.log(JSON.stringify({ok: false, error: {
    stage: error instanceof TutorRunError ? error.stage : 'input preparation',
    repairCount: error instanceof TutorRunError ? error.repairCount : 0,
    ...(error instanceof TutorRunError && error.code === 'live_calls_disabled' ? {code: error.code} : {}),
  }}));
  process.exitCode = 1;
}
