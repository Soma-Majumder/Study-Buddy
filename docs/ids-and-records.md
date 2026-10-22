# Shared IDs and records (S2)

Both agents and the controller use these IDs. Only controller code writes the records in [`db/schema.sql`](../db/schema.sql).

## ID formats

| ID | Format | Who makes it | Example |
|---|---|---|---|
| `lesson_id` | `lesson_<slug>` + integer `version` | A person, when approving a lesson | `lesson_cell_membranes` (v1) |
| `concept_id` | `c_<slug>`, unique across all lessons | A person, in the lesson file | `c_cholesterol_buffer` |
| `learner_id` | `learner_<12 chars>` | Controller | `learner_k3x9a0qz7m2d` |
| `session_id` | `session_<12 chars>` | Controller | `session_q8w1e5r2t6y4` |
| `explanation_id` | `expl_<12 chars>` | Controller | |
| `quiz_id` | `quiz_<12 chars>` | Controller | |
| `question_id` | `q_<12 chars>` | Controller | |
| `attempt_id` | `attempt_<12 chars>` | Controller | |
| `result_id` | `result_<12 chars>` | Controller | |
| `handoff_id` | `handoff_<12 chars>` | Controller | |

Rules:
- The 12 characters are random lowercase letters and digits.
- Agents never invent IDs. Agents may only use `concept_id`s that came from the lesson file or the controller.
- Every ID matches `^[a-z][a-z0-9_]*$`, which is what the handoff schemas in `schemas/v1/` check.
- The handoff examples use short placeholders (`handoff_0001`). Real IDs follow the table above.

## Records (tables)

| Table | What it holds | PRD item |
|---|---|---|
| `lessons`, `concepts` | Approved lesson text, source link, license, credit, adaptation notice, approval record, and its concepts with their source passages. **Source of truth is `lessons/*.json`**; `study_buddy/lessons.py` loads approved files into these tables | lessons |
| `learners` | One row per learner. Every history query is filtered by `learner_id` | learner isolation |
| `study_sessions` | Learner, lesson, status, learning retry count (0 or 1), failure reason | study_sessions |
| `handoff_repairs` | Repair count (0 or 1) per `handoff_id`. Separate from learning retries | repair counts |
| `explanations`, `explanation_concepts` | Explanation text and the concepts it taught | explanations |
| `quizzes`, `questions` | Ten questions per quiz, each tied to one concept, no answers inside | quizzes & questions |
| `answer_keys` | Correct option per question. Backend only, in its own table so it cannot leak by accident | private keys |
| `attempts`, `responses` | A student's submitted answers, graded by the controller | attempts & responses |
| `results` | Score out of 10, feedback, pass/fail for each attempt | scores, feedback |
| `weak_concepts` | Learner, concept, source attempt, lesson, when recorded | weak concepts |

## Session status

`started` → `teaching` → `awaiting_answers` → `grading` → (`reteaching` → `awaiting_answers` → `grading`) → `pending_save` → `completed`

`failed` is used for an unresolved handoff, a missing source, or a failed save, with a plain-language `failure_reason`. A session is only `completed` after the save succeeds. Technical failures never create a score.

## Decisions to confirm

- Database engine is still open in the PRD. The SQL is plain and runs on SQLite.
- Questions have exactly four options (a to d). Say if you want a different count.
- `learner_id` is just an ID for now. How learners sign in is an open PRD decision.
