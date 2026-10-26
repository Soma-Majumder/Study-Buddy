import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {runAssessmentRevision} from './assessment-revision.mjs';
const load = async name => JSON.parse(await readFile(new URL(`../tests/fixtures/quiz_quality/${name}.json`, import.meta.url), 'utf8'));
const fixture = await load('context');
const report = (await load('pass')).report;
function context() {
  const c = structuredClone(fixture.candidate); const canonical = JSON.stringify(c);
  const {questions, quiz_review, ...header} = structuredClone(report);
  header.candidate_hash = createHash('sha256').update('study-buddy-private-quiz/v1\n'+canonical).digest('hex');
  const r = {...structuredClone(report), ...header};
  r.quiz_review.target_diversity='fail';
  r.quiz_review.issues=[{code:'repeated_target',detail:'Fix repeated target.',related_positions:[1,2]}];
  return {original:{controller_context:header,candidate:c,candidate_canonical_json:canonical,
    teaching:{explanation_text:fixture.handoff.payload.explanation_text,
      concept_ids:fixture.handoff.payload.concepts.map(c=>c.concept_id),focus_concept_ids:[]},
    sources:fixture.handoff.sources},review_report:r};
}
const options = fetchImpl => ({allowPaidCalls: true, apiKey:'fake-key',fetchImpl});
const response = text => ({ok:true,json:async()=>({stop_reason:'end_turn',content:[{type:'text',text}],model:'fixture',usage:{output_tokens:1}})});
test('revision sends bound original/report and requests a complete replacement once', async()=>{
  const c=context(); let calls=0;
  const result=await runAssessmentRevision(c,{claudeOptions:options(async(_url,request)=>{
    calls++; const body=JSON.parse(request.body); const data=JSON.parse(body.messages[0].content);
    assert.match(body.system,/single content revision/); assert.match(body.system,/untrusted data/);
    assert.deepEqual(data.candidate,c.original.candidate); assert.deepEqual(data.review_report,c.review_report);
    assert.equal(data.repair_request,undefined); assert.equal(data.candidate_canonical_json,undefined);
    assert.equal(body.max_tokens,8192); assert.equal(body.output_config.effort,'low');
    assert.ok(body.output_config.format.schema.properties.result);
    return response(JSON.stringify({result:c.original.candidate}));
  })});
  assert.equal(calls,1); assert.deepEqual(result.output,c.original.candidate);
});
test('mismatched report or altered original stops before fetch',async()=>{
  for(const mutate of [c=>c.review_report.candidate_hash='0'.repeat(64),c=>c.original.candidate.answer_key[0].rationale+='changed']){
    const c=context(); mutate(c);
    await assert.rejects(runAssessmentRevision(c,{claudeOptions:options(()=>assert.fail('No fetch'))}),e=>e.code==='invalid_input');
  }
});
test('technical correction uses fixed instructions with original data, no malformed output replay',async()=>{
  await runAssessmentRevision(context(),{technicalRepair:true,claudeOptions:options(async(_url,request)=>{
    const data=JSON.parse(JSON.parse(request.body).messages[0].content);
    assert.match(data.repair_request,/shared technical repair/);
    return response('malformed private output');
  })}).then(result=>assert.equal(result.output,null));
});
test('provider failures stop once without leaking raw text',async()=>{
  let calls=0;
  await assert.rejects(runAssessmentRevision(context(),{claudeOptions:options(async()=>{calls++;throw new Error('private provider');})}),
    e=>e.code==='transport_error'&&!JSON.stringify(e).includes('private provider'));
  assert.equal(calls,1);
});

test('revision stops on disabled live calls without invoking transport',async()=>{
  await assert.rejects(runAssessmentRevision(context(),{claudeOptions:{...options(()=>assert.fail('No provider call')),allowPaidCalls:false}}),
    error=>error.code==='live_calls_disabled');
});
