import {readFile} from 'node:fs/promises';
import {isDeepStrictEqual} from 'node:util';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const schema = JSON.parse(await readFile(new URL('../schemas/v1/assessment_to_tutor.schema.json', import.meta.url), 'utf8'));
const ajv = new Ajv2020();
addFormats(ajv);
const matchesSchema = ajv.compile(schema);

// currentAttempt must come from controller-verified records, not Assessment.
// This checks eligibility; the controller must reserve the learning retry.
export function checkReteaching({handoff, currentAttempt, learningRetryCount, sessionId, lesson, sources}) {
  if (!matchesSchema(handoff)) throw new Error('Invalid Assessment return handoff; weak concepts are required.');
  if (learningRetryCount !== 0 || currentAttempt?.round !== 'first') {
    throw new Error('The learning retry is unavailable.');
  }
  const p = handoff.payload;
  if (handoff.context.learner_session_id !== sessionId || handoff.context.round !== 'first' ||
      currentAttempt.sessionId !== sessionId || currentAttempt.lessonId !== lesson.lesson_id ||
      currentAttempt.lessonVersion !== lesson.version ||
      p.lesson_id !== lesson.lesson_id || p.lesson_version !== lesson.version ||
      p.quiz_id !== currentAttempt.quizId || p.attempt_id !== currentAttempt.attemptId ||
      !isDeepStrictEqual(handoff.sources, sources)) {
    throw new Error('Return handoff does not match the current session, lesson, sources, or attempt.');
  }
  if (!Number.isInteger(currentAttempt.score) || currentAttempt.score < 0 || currentAttempt.score >= 8 ||
      p.score !== currentAttempt.score) {
    throw new Error('Reteaching requires a verified score below 8/10.');
  }
  const counts = currentAttempt.missedConceptCounts;
  const allowed = new Set(lesson.concepts.map(c => c.concept_id));
  if (!counts || typeof counts !== 'object' || Array.isArray(counts) ||
      Object.entries(counts).some(([id, count]) => !allowed.has(id) || !Number.isInteger(count) || count < 1) ||
      Object.values(counts).reduce((sum, n) => sum + n, 0) !== 10 - currentAttempt.score) {
    throw new Error('Verified missed-concept counts are missing or inconsistent.');
  }
  const seen = new Set();
  for (const weak of p.weak_concepts) {
    if (!allowed.has(weak.concept_id) || seen.has(weak.concept_id) ||
        counts[weak.concept_id] !== weak.missed_count) {
      throw new Error('Weak concepts do not match the verified missed answers.');
    }
    seen.add(weak.concept_id);
  }
  if (seen.size !== Object.keys(counts).length) {
    throw new Error('Return handoff omitted a verified weak concept.');
  }
  return p.weak_concepts;
}
