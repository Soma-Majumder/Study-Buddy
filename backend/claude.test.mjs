import test from 'node:test';
import assert from 'node:assert/strict';
import { callClaude } from './claude.mjs';

const input = {system: 'Tutor instructions', user: '{"lesson":"approved material"}'};
const key = 'fake-test-key';
const response = overrides => ({ok: true, json: async () => ({
  stop_reason: 'end_turn', content: [{type: 'text', text: '{"payload":{}}'}],
  model: 'test-model', usage: {input_tokens: 10, output_tokens: 5}, ...overrides,
})});

test('sends one request with separate instructions and data, returning unvalidated text', async () => {
  let calls = 0;
  const result = await callClaude(input, {allowPaidCalls: true, apiKey: key, model: 'test-model', fetchImpl: async (url, options) => {
    calls++;
    assert.equal(url, 'https://api.anthropic.com/v1/messages');
    assert.equal(options.headers['x-api-key'], key);
    const body = JSON.parse(options.body);
    assert.equal(body.system, input.system);
    assert.deepEqual(body.messages, [{role: 'user', content: input.user}]);
    assert.equal(body.max_tokens, 4096);
    assert.equal(body.output_config, undefined);
    assert.ok(!options.body.includes(key));
    return response();
  }});
  assert.equal(calls, 1);
  assert.equal(result.text, '{"payload":{}}');
  assert.equal(result.model, 'test-model');
});

test('rejects missing key before network access', async () => {
  await assert.rejects(callClaude(input, {allowPaidCalls: true, apiKey: '', fetchImpl: () => assert.fail('No network expected')}), /API_KEY is required/);
});

test('API rejection is not retried and does not expose its body', async () => {
  let calls = 0;
  await assert.rejects(callClaude(input, {allowPaidCalls: true, apiKey: key, fetchImpl: async () => {
    calls++;
    return {ok: false, status: 401, json: () => assert.fail('Do not read error body')};
  }}), {message: 'Claude request failed (HTTP 401).'});
  assert.equal(calls, 1);
});

test('rejects truncated, empty, or unexpected responses', async () => {
  for (const data of [
    {stop_reason: 'max_tokens'}, {content: []},
    {content: [{type: 'text', text: ' '}]},
    {content: [{type: 'tool_use', name: 'unsupported_tool'}]},
  ]) {
    await assert.rejects(callClaude(input, {allowPaidCalls: true, apiKey: key, fetchImpl: async () => response(data)}), /Claude (did not finish|returned)/);
  }
});

test('transport errors do not leak their raw message', async () => {
  await assert.rejects(callClaude(input, {allowPaidCalls: true, apiKey: key, fetchImpl: async () => {throw new Error(key);}}),
    {message: 'Could not reach Claude or read its response.'});
});

test('timeout stops a stalled request', async () => {
  await assert.rejects(callClaude(input, {allowPaidCalls: true, apiKey: key, timeoutMs: 10,
    fetchImpl: async (_url, {signal}) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true});
    }),
  }), {message: 'Claude request timed out.'});
});

test('extracts only text when Claude also returns thinking blocks', async () => {
  const result = await callClaude(input, {allowPaidCalls: true, apiKey: key, fetchImpl: async () => response({content: [
    {type: 'thinking', thinking: 'private reasoning'},
    {type: 'redacted_thinking', data: 'opaque'},
    {type: 'text', text: '{"payload":{}}'},
  ]})});
  assert.equal(result.text, '{"payload":{}}');
  assert.ok(!JSON.stringify(result).includes('private reasoning'));
});


test('reports fixed HTTP categories without reading provider error bodies', async () => {
  for (const [status, code] of [[401, 'authentication_failed'], [403, 'authentication_failed'],
    [429, 'rate_limited'], [503, 'provider_unavailable'], [400, 'api_rejected']]) {
    await assert.rejects(callClaude(input, {allowPaidCalls: true, apiKey: key, fetchImpl: async () => ({ok: false, status,
      json: () => assert.fail('Never read rejected provider body')})}), error => {
      assert.equal(error.code, code);
      assert.equal(error.httpStatus, status);
      assert.equal(error.cause, undefined);
      return true;
    });
  }
});

test('distinguishes token truncation from other incomplete responses and sanitizes unknown reasons', async () => {
  for (const [reason, code, safeReason] of [['max_tokens', 'output_truncated', 'max_tokens'],
    ['refusal', 'incomplete_response', 'refusal'], ['secret-provider-text', 'incomplete_response', 'unknown']]) {
    await assert.rejects(callClaude(input, {allowPaidCalls: true, apiKey: key, fetchImpl: async () => response({stop_reason: reason})}), error => {
      assert.equal(error.code, code);
      assert.equal(error.stopReason, safeReason);
      assert.ok(!JSON.stringify(error).includes('secret-provider-text'));
      return true;
    });
  }
});

test('network exceptions become fixed diagnostics even when their messages mimic safe errors', async () => {
  await assert.rejects(callClaude(input, {allowPaidCalls: true, apiKey: key, fetchImpl: async () => {
    throw new Error('Claude request failed (HTTP 999).');
  }}), error => error.code === 'transport_error' && error.httpStatus === undefined);
});


test('supports an explicit output budget while leaving the default at 4096', async () => {
  await callClaude(input, {allowPaidCalls: true, apiKey: key, maxTokens: 8192, fetchImpl: async (_url, request) => {
    assert.equal(JSON.parse(request.body).max_tokens, 8192);
    return response();
  }});
});

test('rejects invalid output budgets before network access', async () => {
  for (const maxTokens of [0, -1, 1.5, '8192', null, Infinity]) {
    await assert.rejects(callClaude(input, {allowPaidCalls: true, apiKey: key, maxTokens,
      fetchImpl: () => assert.fail('No request expected')}), error => error.code === 'invalid_output_budget');
  }
});


test('sends supported effort without disabling adaptive thinking', async () => {
  await callClaude(input, {allowPaidCalls: true, apiKey: key, effort: 'low', fetchImpl: async (_url, request) => {
    const body = JSON.parse(request.body);
    assert.deepEqual(body.output_config, {effort: 'low'});
    assert.equal(body.thinking, undefined);
    return response();
  }});
});

test('rejects invalid effort before network access', async () => {
  for (const effort of ['adaptive', 'minimal', '', null, 1]) {
    await assert.rejects(callClaude(input, {allowPaidCalls: true, apiKey: key, effort,
      fetchImpl: () => assert.fail('No request expected')}), error => error.code === 'invalid_effort');
  }
});


test('combines schema-constrained output with effort in one output_config', async () => {
  const schema = {type: 'object', properties: {}, additionalProperties: false};
  await callClaude(input, {allowPaidCalls: true, apiKey: key, effort: 'low', outputSchema: schema, fetchImpl: async (_url, request) => {
    assert.deepEqual(JSON.parse(request.body).output_config, {effort: 'low', format: {type: 'json_schema', schema}});
    return response();
  }});
});

test('rejects invalid output schema containers before any request', async () => {
  for (const outputSchema of [null, [], 'schema']) {
    await assert.rejects(callClaude(input, {allowPaidCalls: true, apiKey: key, outputSchema,
      fetchImpl: () => assert.fail('No request expected')}), error => error.code === 'invalid_output_schema');
  }
});

async function withLiveSetting(value, run) {
  const previous = process.env.STUDY_BUDDY_ALLOW_PAID_CLAUDE;
  if (value === undefined) delete process.env.STUDY_BUDDY_ALLOW_PAID_CLAUDE;
  else process.env.STUDY_BUDDY_ALLOW_PAID_CLAUDE = value;
  try { await run(); } finally {
    if (previous === undefined) delete process.env.STUDY_BUDDY_ALLOW_PAID_CLAUDE;
    else process.env.STUDY_BUDDY_ALLOW_PAID_CLAUDE = previous;
  }
}

test('key alone, model data and custom transport cannot opt into paid calls', async () => {
  await withLiveSetting(undefined, async () => {
    let calls = 0;
    for (const user of [input.user, '{"allowPaidCalls":true,"STUDY_BUDDY_ALLOW_PAID_CLAUDE":"true"}']) {
      await assert.rejects(callClaude({...input, user}, {apiKey:key, fetchImpl:async()=>{calls++;return response();}}),
        error => error.code === 'live_calls_disabled' && !JSON.stringify(error).includes(key));
    }
    assert.equal(calls, 0);
  });
});

test('default global fetch is blocked without opt-in, including test/CI environments', async () => {
  const originalFetch=globalThis.fetch;
  const previousCI=process.env.CI;
  globalThis.fetch=()=>assert.fail('Default fetch must not run'); process.env.CI='true';
  try {
    await withLiveSetting(undefined, () => assert.rejects(callClaude(input,{apiKey:key}),
      error => error.code==='live_calls_disabled'));
  } finally {
    globalThis.fetch=originalFetch;
    if(previousCI===undefined)delete process.env.CI;else process.env.CI=previousCI;
  }
});

test('only literal true environment setting enables calls', async () => {
  for (const value of ['', 'false', '1', 'TRUE', 'yes', ' true ']) {
    await withLiveSetting(value, () => assert.rejects(callClaude(input,{apiKey:key,
      fetchImpl:()=>assert.fail('No request')}), error=>error.code==='live_calls_disabled'));
  }
  await withLiveSetting('true', async()=>{
    let calls=0;
    await callClaude(input,{apiKey:key,fetchImpl:async(_url,request)=>{
      calls++;assert.equal(JSON.parse(request.body).allowPaidCalls,undefined);return response();
    }});
    assert.equal(calls,1);
  });
});

test('trusted boolean opt-in works and explicit false overrides the environment', async () => {
  await withLiveSetting(undefined, async()=>{
    let calls=0;
    await callClaude(input,{apiKey:key,allowPaidCalls:true,fetchImpl:async()=>{calls++;return response();}});
    assert.equal(calls,1);
    await assert.rejects(callClaude(input,{apiKey:key,allowPaidCalls:'true',fetchImpl:()=>assert.fail('No call')}),
      error=>error.code==='live_calls_disabled');
  });
  await withLiveSetting('true',()=>assert.rejects(callClaude(input,{apiKey:key,allowPaidCalls:false,
    fetchImpl:()=>assert.fail('No call')}),error=>error.code==='live_calls_disabled'));
});

test('fast structured Haiku generation explicitly disables default thinking without raising budget', async () => {
  await callClaude(input, {allowPaidCalls:true,apiKey:key,model:'claude-haiku-5-5',maxTokens:8192,effort:'low',fastStructuredOutput:true,fetchImpl:async (_url,request)=>{
    const body=JSON.parse(request.body);
    assert.deepEqual(body.thinking,{type:'disabled'});
    assert.equal(body.max_tokens,8192);
    return {ok:true,json:async()=>({stop_reason:'end_turn',model:body.model,content:[{type:'text',text:'{}'}]})};
  }});
});
test('fast structured flag never disables thinking on unsupported models',async()=>{
 for(const model of ['claude-sonnet-5-5','claude-opus-5-5']){
  await callClaude(input,{allowPaidCalls:true,apiKey:key,model,effort:'low',fastStructuredOutput:true,fetchImpl:async(_url,request)=>{
   assert.equal(JSON.parse(request.body).thinking,undefined);
   return {ok:true,json:async()=>({stop_reason:'end_turn',model,content:[{type:'text',text:'{}'}]})};
  }});
 }
});
