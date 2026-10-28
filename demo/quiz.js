'use strict';
const $ = (id) => document.getElementById(id);
function node(tag, text, className) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el; }
let quiz, index = 0, answers = {};
function renderTutor() {
  const t = quiz.tutor, root = node('div', undefined, 'tutor');
  $('progress-text').textContent = 'Step 1 of 2 · Read the Tutor’s summary'; $('bar-fill').style.width = '0';
  root.append(node('h2', quiz.title), node('p', 'Your Tutor’s explanation, in short sections. Read them first; the quiz covers only what is here.', 'why'));
  t.sections.forEach((sec, i) => {
    const card = node('section', undefined, 'sec'); card.append(node('span', `${i + 1} / ${t.sections.length}`, 'sec-num'), node('h3', sec.heading), node('p', sec.text), node('p', 'Key idea: ' + sec.key_idea, 'idea')); root.append(card);
  });
  root.append(node('p', 'Adapted from ' + t.attribution + '.', 'why'));
  const go = node('button', 'I’m ready — start the quiz', 'btn'); go.onclick = () => { index = 0; answers = {}; renderQuestion(); window.scrollTo(0, 0); };
  root.append(go); $('stage').replaceChildren(root);
}
function renderQuestion() {
  const q = quiz.questions[index], total = quiz.questions.length, stage = $('stage');
  $('progress-text').textContent = `Question ${index + 1} of ${total} · ${Object.keys(answers).length} answered`;
  $('bar-fill').style.width = `${(index / total) * 100}%`;
  const card = node('div', undefined, 'q-card'); card.append(node('h2', q.prompt));
  Object.entries(q.options).forEach(([key, text]) => {
    const b = node('button', undefined, 'opt' + (answers[q.position] === key ? ' sel' : '')); b.type = 'button';
    b.append(node('b', key.toUpperCase()), node('span', text));
    b.addEventListener('click', () => { answers[q.position] = key; renderQuestion(); }); card.append(b);
  });
  const nav = node('div', undefined, 'nav');
  const back = node('button', 'Back', 'btn ghost'); back.disabled = index === 0; back.onclick = () => { index--; renderQuestion(); };
  const last = index === total - 1;
  const next = node('button', last ? 'Submit answers' : 'Next', 'btn'); next.disabled = !answers[q.position] || (last && Object.keys(answers).length < total);
  next.onclick = () => { if (last) renderResults(); else { index++; renderQuestion(); } };
  nav.append(back, next); card.append(nav); stage.replaceChildren(card); card.querySelector('.opt').focus?.();
}
function renderResults() {
  const keyFor = Object.fromEntries(quiz.answer_key.map(k => [k.position, k]));
  const score = quiz.questions.filter(q => answers[q.position] === keyFor[q.position].correct_option).length;
  const passed = score >= quiz.pass_mark;
  $('bar-fill').style.width = '100%'; $('progress-text').textContent = 'Results';
  const root = node('div'); root.append(node('div', `${score}/10`, 'score'),
    node('span', passed ? 'Passed' : `Not yet — ${quiz.pass_mark}/10 needed. Review below, then retry.`, 'verdict ' + (passed ? 'pass' : 'retry')));
  quiz.questions.forEach(q => {
    const k = keyFor[q.position], ok = answers[q.position] === k.correct_option;
    const r = node('div', undefined, 'review ' + (ok ? 'ok' : 'bad'));
    r.append(node('h3', `${q.position}. ${q.prompt}`));
    r.append(node('p', ok ? `✓ ${q.options[k.correct_option]}` : `✗ You chose: ${q.options[answers[q.position]]}`));
    if (!ok) r.append(node('p', `Correct answer: ${q.options[k.correct_option]}`));
    r.append(node('p', k.rationale, 'why')); root.append(r);
  });
  const again = node('button', 'Review the summary and retry', 'btn'); again.onclick = () => { index = 0; answers = {}; renderTutor(); };
  root.append(again); $('stage').replaceChildren(root); window.scrollTo(0, 0);
}
fetch('/sample-quiz.json').then(r => r.json()).then(data => { quiz = data; $('lesson-title').textContent = quiz.title.toUpperCase(); renderTutor(); })
  .catch(() => { $('stage').textContent = 'Could not load the quiz.'; });
