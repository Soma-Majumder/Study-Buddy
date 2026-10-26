// Backend-only stdin/stdout bridge. Its output can include private quiz keys.
import { runAssessmentQuiz, AssessmentRunError } from './assessment.mjs';

try {
  let text = '';
  for await (const chunk of process.stdin) text += chunk;
  const {handoff, repair_request = null} = JSON.parse(text);
  const result = await runAssessmentQuiz(handoff, {repairRequest: repair_request});
  console.log(JSON.stringify({ok: true, result}));
} catch (error) {
  console.log(JSON.stringify({ok: false, error: {
    stage: error instanceof AssessmentRunError ? error.stage : 'input preparation',
  }}));
  process.exitCode = 1;
}
