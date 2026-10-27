"""Offline checks of the explicitly loaded evaluation-only response recorder."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class EvalCaptureTests(unittest.TestCase):
    def test_captures_text_without_thinking_headers_or_error_bodies(self):
        script = r'''import {readFile} from 'node:fs/promises';
let calls = 0;
globalThis.fetch = async () => {
  calls++;
  if (calls === 2) return {ok: false, clone: () => {throw new Error('Do not read error bodies');}};
  return {ok: true, clone: () => ({json: async () => ({stop_reason: 'end_turn',
    content: [{type: 'thinking', thinking: 'private-reasoning'}, {type: 'text', text: '```json\n{}\n```'}],
    headers: {'x-api-key': 'fake-secret'}})})};
};
await import('./backend/evals/capture-response.mjs');
await fetch('https://example.invalid', {headers: {'x-api-key': 'fake-secret'}});
await fetch('https://example.invalid');
console.log(await readFile(process.env.STUDY_BUDDY_EVAL_CAPTURE_PATH, 'utf8'));
'''
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'capture.jsonl')
            env = dict(os.environ, STUDY_BUDDY_EVAL_CAPTURE_PATH=path)
            process = subprocess.run([os.environ.get('STUDY_BUDDY_NODE', 'node'), '--input-type=module', '-e', script],
                                     cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=10)
            row = json.loads(process.stdout)
            self.assertEqual(row, dict(text='```json\n{}\n```', stop_reason='end_turn', usage={}))
            self.assertNotIn('private-reasoning', process.stdout)
            self.assertNotIn('fake-secret', process.stdout)

    def test_captures_only_nonnegative_integer_usage_including_truncated_responses(self):
        script = r'''import {readFile} from 'node:fs/promises';
globalThis.fetch = async () => ({ok: true, clone: () => ({json: async () => ({
  stop_reason: 'max_tokens', content: [{type: 'text', text: '{partial'}], usage: {
    input_tokens: 300, output_tokens: 8192, cache_creation_input_tokens: 0, cache_read_input_tokens: -1,
    output_tokens_details: {thinking_tokens: 6500, private_reasoning: 'do-not-capture'},
    unknown_metadata: 'do-not-capture',
  },
})})});
await import('./backend/evals/capture-response.mjs');
await fetch('https://example.invalid');
console.log(await readFile(process.env.STUDY_BUDDY_EVAL_CAPTURE_PATH, 'utf8'));
'''
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, STUDY_BUDDY_EVAL_CAPTURE_PATH=str(Path(folder) / 'usage.jsonl'))
            result = subprocess.run([os.environ.get('STUDY_BUDDY_NODE', 'node'), '--input-type=module', '-e', script],
                                    cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=10)
            row = json.loads(result.stdout)
            self.assertEqual(row['usage'], dict(input_tokens=300, output_tokens=8192,
                                              cache_creation_input_tokens=0, thinking_tokens=6500))
            self.assertEqual(row['stop_reason'], 'max_tokens')
            self.assertNotIn('do-not-capture', result.stdout)

    def test_usage_totals_include_repairs_and_keep_missing_counts_unknown(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('controller_eval', ROOT / 'backend/evals/assessment_controller.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        totals = module.usage_totals([
            dict(token_usage=dict(input_tokens=10, output_tokens=100, thinking_tokens=80)),
            dict(token_usage=dict(input_tokens=20, output_tokens=50, thinking_tokens=20)),
        ])
        self.assertEqual(totals['input_tokens'], 30)
        self.assertEqual(totals['output_tokens'], 150)
        self.assertEqual(totals['thinking_tokens'], 100)
        self.assertAlmostEqual(totals['thinking_share_of_output'], 2 / 3)
        self.assertIsNone(totals['cache_read_input_tokens'])
        self.assertIsNone(module.usage_totals([dict(token_usage=dict(output_tokens=5)), {}])['output_tokens'])
        self.assertIsNone(module.usage_totals([])['input_tokens'])
        self.assertIsNone(module.usage_totals([dict(token_usage=dict(output_tokens=5, thinking_tokens=6))])['thinking_share_of_output'])

    def test_usage_only_capture_survives_real_transport_truncation_without_private_text(self):
        script = r'''import {readFile, stat} from 'node:fs/promises';
const body = {stop_reason:'max_tokens', content:[
  {type:'thinking', thinking:'private-reasoning-sentinel'},
  {type:'text', text:'private-quiz-output-sentinel'}],
  usage:{input_tokens:500,output_tokens:8192,cache_read_input_tokens:-1,
    output_tokens_details:{thinking_tokens:6100,private_reasoning:'private-reasoning-sentinel'}}};
globalThis.fetch = async () => new Response(JSON.stringify(body), {status:200});
await import('./backend/evals/capture-usage.mjs');
const {callClaude} = await import('./backend/claude.mjs');
let code;
try {await callClaude({system:'private-prompt-sentinel',user:'private-input-sentinel'},
  {apiKey:'fake-secret',allowPaidCalls:true});}
catch (error) {code=error.code;}
const row=JSON.parse(await readFile(process.env.STUDY_BUDDY_EVAL_CAPTURE_PATH,'utf8'));
console.log(JSON.stringify({code,row,mode:(await stat(process.env.STUDY_BUDDY_EVAL_CAPTURE_PATH)).mode & 0o777}));
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'usage.jsonl'
            env = dict(os.environ, STUDY_BUDDY_EVAL_CAPTURE_PATH=str(path), STUDY_BUDDY_EVAL_PHASE='review_revision')
            result = subprocess.run([os.environ.get('STUDY_BUDDY_NODE','node'),'--input-type=module','-e',script],
                cwd=ROOT,env=env,capture_output=True,text=True,check=True,timeout=10)
            output=json.loads(result.stdout)
            self.assertEqual(output['code'],'output_truncated')
            self.assertEqual(output['mode'],0o600)
            self.assertEqual(output['row'],dict(phase='review_revision',usage=dict(input_tokens=500,output_tokens=8192,thinking_tokens=6100),
                text_characters=len('private-quiz-output-sentinel'),stop_reason='max_tokens'))
            for forbidden in ('private-reasoning-sentinel','private-quiz-output-sentinel','private-prompt-sentinel','private-input-sentinel','fake-secret'):
                self.assertNotIn(forbidden,result.stdout)

    def test_usage_only_capture_ignores_http_errors_and_keeps_absent_usage_unknown(self):
        script = r'''import {readFile} from 'node:fs/promises';
let calls=0;
globalThis.fetch=async()=>++calls===1 ? {ok:false,clone:()=>{throw new Error('Do not read error body');}} :
 {ok:true,clone:()=>({json:async()=>({stop_reason:'end_turn',content:[{type:'text',text:'private-result'}]})})};
await import('./backend/evals/capture-usage.mjs');
await fetch('https://example.invalid',{headers:{'x-api-key':'fake-secret'}});
await fetch('https://example.invalid');
console.log(await readFile(process.env.STUDY_BUDDY_EVAL_CAPTURE_PATH,'utf8'));
'''
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,STUDY_BUDDY_EVAL_CAPTURE_PATH=str(Path(folder)/'usage.jsonl'),STUDY_BUDDY_EVAL_PHASE='untrusted-phase')
            result=subprocess.run([os.environ.get('STUDY_BUDDY_NODE','node'),'--input-type=module','-e',script],
                cwd=ROOT,env=env,capture_output=True,text=True,check=True,timeout=10)
            row=json.loads(result.stdout)
            self.assertEqual(row,dict(phase='unknown',usage={},text_characters=14,stop_reason='end_turn'))
            self.assertNotIn('private-result',result.stdout)

    def test_eval_usage_capture_still_requires_paid_authorization_and_exclusive_capture_mode(self):
        command=[__import__('sys').executable,'-B',str(ROOT/'backend/evals/assessment_controller.py')]
        missing=subprocess.run(command+['--capture-token-usage'],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(missing.returncode,0)
        self.assertIn('--live --quality-gate are required',missing.stderr)
        both=subprocess.run(command+['--capture-token-usage','--capture-response-text'],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(both.returncode,0)
        self.assertIn('not allowed with argument',both.stderr)
