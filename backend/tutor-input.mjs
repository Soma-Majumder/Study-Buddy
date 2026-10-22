import { readFile } from 'node:fs/promises';
import { loadApprovedLesson } from './lessons.mjs';
import { checkReteaching } from './reteaching.mjs';
import { QUIZ_EXPECTED_OUTPUT } from './handoff-policy.mjs';

const projectFile = path => new URL(path, import.meta.url);

// Build first-round or focused reteaching input. Never contacts Claude.
export async function buildTutorInput({ lessonId, sessionId, handoffId, explanationId, round = 'first', returnHandoff, currentAttempt, learningRetryCount }) {
  if (!['first', 'retry'].includes(round)) throw new Error('Unknown Tutor round.');
  if (round === 'first' && (returnHandoff !== undefined || currentAttempt !== undefined)) {
    throw new Error('First-round input must not include a reteaching handoff.');
  }
  // IDs belong to the controller; the model must receive them, not invent them.
  for (const [value, prefix] of [
    [sessionId, 'session'], [handoffId, 'handoff'], [explanationId, 'expl'],
  ]) {
    if (typeof value !== 'string' || !new RegExp(`^${prefix}_[a-z0-9]{12}$`).test(value)) {
      throw new Error(`A controller-supplied ${prefix} ID with 12 lowercase letters/digits is required.`);
    }
  }

  const lesson = await loadApprovedLesson(lessonId);
  if (!Number.isInteger(lesson.version) || lesson.version < 1 ||
      !lesson.source_text?.trim() || !lesson.source_passages ||
      !lesson.source_url || !lesson.license || !lesson.book || !lesson.authors ||
      !Array.isArray(lesson.concepts) || lesson.concepts.length === 0) {
    throw new Error('Approved lesson is missing required teaching or source information.');
  }
  const ids = new Set();
  for (const concept of lesson.concepts) {
    if (!/^c_[a-z0-9_]+$/.test(concept.concept_id ?? '') ||
        !concept.name?.trim() || !concept.summary?.trim() ||
        !lesson.source_passages[concept.concept_id]?.trim() || ids.has(concept.concept_id)) {
      throw new Error('Approved lesson has an invalid, empty, or duplicate concept.');
    }
    ids.add(concept.concept_id);
  }

  const system = await readFile(projectFile('./prompts/tutor-system.md'), 'utf8');
  const schema = JSON.parse(await readFile(projectFile('../schemas/v1/tutor_to_assessment.schema.json'), 'utf8'));
  const source = {
    lesson_id: lesson.lesson_id,
    lesson_version: lesson.version,
    url: lesson.source_url,
    license: lesson.license,
    attribution: `${lesson.book}, ${lesson.authors}`,
  };

  const weak = round === 'retry' ? checkReteaching({
    handoff: returnHandoff, currentAttempt, learningRetryCount,
    sessionId, lesson, sources: [source],
  }) : [];
  const focusIds = weak.map(c => c.concept_id);

  // Keep trusted instructions separate from lesson data. Do not include .env,
  // unrelated student history, quiz answer keys, or unreviewed source-page text.
  const user = JSON.stringify({
    controller_context: {
      handoff_id: handoffId,
      learner_session_id: sessionId,
      round,
      explanation_id: explanationId,
    },
    approved_lesson: {
      lesson_id: lesson.lesson_id,
      version: lesson.version,
      title: lesson.title,
      source_text: round === 'retry'
        ? focusIds.map(id => lesson.source_passages[id]).join('\n\n')
        : lesson.source_text,
      concepts: round === 'retry'
        ? lesson.concepts.filter(c => focusIds.includes(c.concept_id))
        : lesson.concepts,
    },
    sources: [source],
    output_schema: schema,
    expected_output: QUIZ_EXPECTED_OUTPUT,
    ...(round === 'retry' ? {
      focus_concept_ids: focusIds,
      feedback: weak.map(c => ({concept_id: c.concept_id, note: c.note || ''})),
    } : {}),
  });

  return { system, user };
}
