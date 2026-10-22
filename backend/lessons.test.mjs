import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { loadApprovedLesson } from './lessons.mjs';

test('loads the approved lesson with its source and concept IDs', async () => {
  const lesson = await loadApprovedLesson('lesson_cell_membranes');
  assert.equal(lesson.approved, true);
  assert.equal(lesson.version, 2);
  assert.equal(lesson.source_url, 'https://cellandmolecularbiology.pressbooks.tru.ca/chapter/2-1/');
  assert.equal(lesson.concepts.length, 7);
  assert.ok(lesson.concepts.some(c => c.concept_id === 'c_cholesterol_buffer'));
});

test('missing lesson stops instead of returning a substitute', async () => {
  await assert.rejects(loadApprovedLesson('lesson_missing'), /Lesson not found/);
});

test('rejects file paths as lesson IDs', async () => {
  await assert.rejects(loadApprovedLesson('../.env'), /valid lesson ID/);
});

test('rejects an unapproved lesson and an ambiguous duplicate ID', async () => {
  const folder = await mkdtemp(join(tmpdir(), 'study-buddy-lessons-'));
  const directory = pathToFileURL(folder + '/');
  try {
    await writeFile(join(folder, 'draft.json'), JSON.stringify({lesson_id: 'lesson_draft', approved: false}));
    await assert.rejects(loadApprovedLesson('lesson_draft', directory), /not approved/);
    await writeFile(join(folder, 'duplicate.json'), JSON.stringify({lesson_id: 'lesson_draft', approved: true}));
    await assert.rejects(loadApprovedLesson('lesson_draft', directory), /Duplicate lesson ID/);
  } finally {
    await rm(folder, {recursive: true, force: true});
  }
});
