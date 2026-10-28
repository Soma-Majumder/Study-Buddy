// Private stdin/stdout protocol. No credentials or provider bodies are emitted.
import {createInterface} from 'node:readline';
import {runTutor, TutorRunError} from './tutor.mjs';
const reader = createInterface({input:process.stdin, crlfDelay:Infinity});
const lines = reader[Symbol.asyncIterator]();
try {
  const first = await lines.next();
  const body = JSON.parse(first.value);
  const managed = body?.protocol === 'durable-tutor-v1';
  const state = {count:0};
  if (managed) state.reserve = async () => {
    console.log(JSON.stringify({event:'reserve_tutor_repair'}));
    const ack = await lines.next();
    return !ack.done && JSON.parse(ack.value)?.repair_reserved === true;
  };
  const result = await runTutor(managed ? body.context : body, {}, state);
  console.log(JSON.stringify({ok:true, result}));
} catch (error) {
  console.log(JSON.stringify({ok:false, error:{
    stage:error instanceof TutorRunError ? error.stage : 'input preparation',
    repairCount:error instanceof TutorRunError ? error.repairCount : 0,
    ...(error instanceof TutorRunError && error.code === 'live_calls_disabled' ? {code:error.code} : {}),
  }}));
  process.exitCode = 1;
} finally {
  reader.close();
}
