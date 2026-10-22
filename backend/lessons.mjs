import { readFile, readdir } from 'node:fs/promises';

const defaultDirectory = new URL('../lessons/', import.meta.url);

// Look up the lesson's stored ID, rather than treating input as a file path.
export async function loadApprovedLesson(lessonId, directory = defaultDirectory) {
  if (typeof lessonId !== 'string' || !/^lesson_[a-z0-9_]+$/.test(lessonId)) {
    throw new Error('A valid lesson ID is required.');
  }

  const matches = [];
  for (const filename of await readdir(directory)) {
    if (!filename.endsWith('.json')) continue;
    const lesson = JSON.parse(await readFile(new URL(filename, directory), 'utf8'));
    if (lesson.lesson_id === lessonId) matches.push(lesson);
  }

  if (matches.length === 0) throw new Error(`Lesson not found: ${lessonId}`);
  if (matches.length > 1) throw new Error(`Duplicate lesson ID: ${lessonId}`);
  const lesson = matches[0];
  if (lesson.approved !== true) throw new Error(`Lesson is not approved: ${lessonId}`);
  return lesson;
}
