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
            self.assertEqual(row, dict(text='```json\n{}\n```', stop_reason='end_turn'))
            self.assertNotIn('private-reasoning', process.stdout)
            self.assertNotIn('fake-secret', process.stdout)
