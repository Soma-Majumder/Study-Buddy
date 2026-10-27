'use strict';
const scenarios = [
  ['pass', 'Pass the first quiz', '8/10 → feedback → saved summary'],
  ['practice', 'Practice missed concepts', '6/10 → reteach → one retry → summary'],
  ['recovery', 'Recover a failed save', 'Keep progress and resume the summary'],
  ['crash', 'Expire an abandoned start', 'Deadline → failed session, no Tutor call'],
  ['duplicates', 'Send duplicate requests', 'Reuse the session and original answers'],
];
const $ = (id) => document.getElementById(id);
let token, busy = false;
function node(tag, text, className) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el; }
function setBusy(value) { busy = value; document.querySelectorAll('button').forEach(b => b.disabled = value || !token); }
function label(name) { return scenarios.find(s => s[0] === name)[1]; }
function metric(title, value) { const el = node('div', undefined, 'metric'); el.append(node('label', title), node('strong', value)); return el; }
function render(data) {
  const r = data.report;
  $('results-title').textContent = label(r.scenario);
  $('result-badge').textContent = 'VERIFIED'; $('result-badge').className = 'status verified';
  $('live-status').textContent = r.scenario === 'crash' ? 'Expected failure saved correctly. The abandoned session cannot restart Tutor.' : 'Scenario completed and saved state verified through a read-only reconnect.';
  const root = $('result-content'); root.replaceChildren();
  const metrics = node('div', undefined, 'metrics');
  metrics.append(metric('Session state', r.status === 'failed' ? 'Failed safely' : 'Completed'), metric('Quiz scores', r.scores.length ? r.scores.map(x => `${x}/10`).join(' → ') : 'No quiz'), metric('Paid calls', String(r.paid_calls)));
  root.append(metrics, node('div', 'Fixture agent calls', 'call-title'));
  const calls = node('div', undefined, 'calls');
  Object.entries(r.fixture_calls).forEach(([key,value]) => calls.append(node('span', `${key}: ${value}`, 'call'))); root.append(calls);
  root.append(node('p', 'Fresh temporary database used and removed. Your application database was untouched.', 'notice'));
  const records = node('details'); records.append(node('summary', 'Saved record counts')); const table = node('table');
  Object.entries(r.rows).forEach(([key,value]) => { const tr = node('tr'); tr.append(node('td', key.replaceAll('_', ' ')), node('td', String(value))); table.append(tr); }); records.append(table); root.append(records);
  const trace = node('details'); trace.open = true; trace.append(node('summary', 'Controller trace')); const list = node('ol', undefined, 'trace');
  data.trace.filter(line => !line.startsWith('Flow:') && !line.startsWith('Disposable') && !line.startsWith('Saved controller trace:')).forEach(line => list.append(node('li', line.trim())));
  trace.append(list); root.append(trace);
}
async function run(name) {
  document.querySelectorAll('.scenario').forEach(b => b.classList.toggle('selected', b.dataset.scenario === name));
  $('results-title').textContent = label(name); $('result-badge').textContent = 'RUNNING'; $('result-badge').className = 'status';
  $('live-status').textContent = 'Running fixture agents and checking saved controller state…';
  $('result-content').replaceChildren(node('p', 'Preparing a fresh disposable database…', 'notice'));
  const response = await fetch('/api/run', {method:'POST', headers:{'Content-Type':'application/json','X-Demo-Token':token}, body:JSON.stringify({scenario:name})});
  const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Scenario failed.'); render(data);
  $('history').hidden = false; $('history-list').append(node('li', `✓ ${label(name)} — verified`));
}
async function execute(names) {
  if (busy) return;
  setBusy(true); $('history-list').replaceChildren(); $('history').hidden = true;
  try { for (const name of names) await run(name); }
  catch (error) { $('result-badge').textContent = 'ERROR'; $('result-badge').className = 'status error'; $('live-status').textContent = error.message; $('result-content').replaceChildren(node('p', 'Check that the local server is running, then try again. A failed run is not marked verified.', 'notice')); }
  finally { setBusy(false); }
}
scenarios.forEach(([name,title,description], index) => {
  const button = node('button', undefined, 'scenario'); button.type = 'button'; button.disabled = true; button.dataset.scenario = name;
  const text = node('span'); text.append(node('strong',title),node('small',description)); button.append(node('span',String(index+1).padStart(2,'0'),'scenario-number'),text,node('span','↗','arrow'));
  button.addEventListener('click', () => execute([name])); $('scenario-list').append(button);
});
$('run-all').addEventListener('click', () => execute(scenarios.map(s => s[0])));
fetch('/api/config').then(response => { if (!response.ok) throw new Error('Could not connect to the local server.'); return response.json(); }).then(config => { token = config.token; setBusy(false); }).catch(error => { $('live-status').textContent = error.message; });
