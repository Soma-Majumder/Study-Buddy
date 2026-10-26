import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import Ajv2020 from 'ajv/dist/2020.js';

const read = async path => JSON.parse(await readFile(new URL(path, import.meta.url), 'utf8'));
const outputSchema = await read('../schemas/v1/quiz_quality_review.schema.json');
const quizSchema = await read('../schemas/v1/quiz_output.schema.json');
const ajv = new Ajv2020({allErrors: true});
const validQuiz = ajv.compile(quizSchema);
const headerKeys = outputSchema.required.filter(k => !['questions', 'quiz_review'].includes(k));
const validHeader = ajv.compile({type: 'object', additionalProperties: false,
  required: headerKeys, properties: Object.fromEntries(headerKeys.map(k => [k, outputSchema.properties[k]])),
  $defs: outputSchema.$defs});
const system = await readFile(new URL('./prompts/reviewer-system.md', import.meta.url), 'utf8');
const reject = () => { throw new Error('Reviewer requires an intact private controller context.'); };
const nonblank = value => typeof value === 'string' && value.trim().length > 0;

// Backend-only. Structure/hash validation is not ownership or dispatch permission.
// Use build_private_reviewer_context with the original reservation grant.
export function buildReviewerInput(context) {
  if (!context || typeof context !== 'object') reject();
  const {controller_context: h, teaching: t, sources, candidate: c, candidate_canonical_json: canonical} = context;
  if (!validHeader(h) || !validQuiz(c) || typeof canonical !== 'string') reject();
  // Hash the original Python serialization, avoiding cross-language number formatting differences.
  let decoded;
  try { decoded = JSON.parse(canonical); } catch { reject(); }
  if (!isDeepStrictEqual(decoded, c) || createHash('sha256').update('study-buddy-private-quiz/v1\n' + canonical).digest('hex') !== h.candidate_hash) reject();
  if (c.handoff_id !== h.handoff_id || c.explanation_id !== h.explanation_id || c.round !== h.round) reject();
  if (!t || !nonblank(t.explanation_text) || !Array.isArray(t.concept_ids) || !t.concept_ids.length ||
      t.concept_ids.some(id => typeof id !== 'string' || !/^[a-z][a-z0-9_]*$/.test(id)) ||
      new Set(t.concept_ids).size !== t.concept_ids.length || !Array.isArray(t.focus_concept_ids)) reject();
  const taught = new Set(t.concept_ids);
  const focus = t.focus_concept_ids;
  if (h.round === 'first' ? focus.length !== 0 : focus.length !== taught.size || new Set(focus).size !== focus.length || focus.some(id => !taught.has(id))) reject();
  if (!Array.isArray(sources) || sources.length !== 1 || sources.some(s =>
    !s || s.lesson_id !== h.lesson_id || s.lesson_version !== h.lesson_version ||
    !['url', 'license', 'attribution'].every(k => nonblank(s[k])))) reject();
  if (new Set(c.quiz.questions.map(q => q.position)).size !== 10 || new Set(c.answer_key.map(k => k.position)).size !== 10) reject();
  for (const q of c.quiz.questions) {
    const options = Object.values(q.options);
    if (!taught.has(q.concept_id) || !nonblank(q.prompt) || options.some(o => !nonblank(o)) ||
        new Set(options.map(o => o.trim().toLowerCase())).size !== 4) reject();
  }
  if (c.answer_key.some(k => !nonblank(k.rationale))) reject();
  // Allowlist fields: no call grants, learner IDs, previous reports, or generator history.
  return {system, user: JSON.stringify({controller_context: h,
    teaching: {explanation_text: t.explanation_text, concept_ids: t.concept_ids, focus_concept_ids: focus},
    sources: sources.map(({lesson_id, lesson_version, url, license, attribution}) => ({lesson_id, lesson_version, url, license, attribution})),
    candidate: c, output_schema: outputSchema})};
}
