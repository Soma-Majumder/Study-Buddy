# Assessment live quiz review — October 26, 2026

Scope: one first-round generation from the hand-written Tutor fixture, not a live Tutor run or full study session. Model: claude-haiku-5-5. One paid call, no repair. Latency: 12,946 ms. Provider-reported input tokens: 3,461; output tokens: 3,257 (including 1,371 thinking tokens). No private reasoning or credentials were recorded.

Report: `results/2026-10-26T18-33-25-979Z-assessment-quiz.json`. The saved prompt hash identifies the pre-adjustment prompt used for this sample. Review is an assistant inspection, not independent educator validation.

## Result: failed format validation; do not show this quiz to a learner

Soma's existing `scripts/validate_quiz.py` rejected two extra properties: `answer_key_placeholder` inside `quiz` and `concept_id` in answer-key position 2. No output was sanitized or accepted despite those errors. The live runner makes one call only; the controller was not invoked and no study-session records were created. The production controller would handle invalid output using its existing remaining-repair policy.

| Criterion | Finding |
| --- | --- |
| Size, positions, keys | Inspection found ten questions and ten matching key positions. Formal overall validation still fails due to extra fields. |
| Correctness and grounding | All ten selected answers and rationales appear supported by the fixture explanation: heads/bilayer (1–2), saturation/kinks (3–4), fluidity factors (5), cholesterol buffering (6–7), fluid mosaic (8), flipping enzymes (9), recognition (10). This evaluates agreement with the explanation, not independent scientific source verification. |
| Coverage | All seven taught concept IDs appear. No exact repeated question; questions 1–2 overlap on bilayer arrangement. |
| Clarity | Mostly short, understandable stems. Question 9 asks “why” but primarily checks recall that enzymes help flipping, rather than an underlying mechanism not taught in the explanation. |
| Distractors | Needs improvement. Q3 “tails attached to cholesterol” introduces an untaught relationship; Q6 “makes the membrane fluid even faster” is awkward, and “breaks the bilayer apart” is too obviously wrong. Several other choices are easy unrelated contrasts. |
| Answer distribution | a: 3, b: 3, c: 2, d: 2; acceptable for this sample, not a measure of content quality. |
| Privacy | Real private keys are confined to answer_key in the generated object; the extra placeholder still violates the contract. Report is a developer evaluation artifact containing keys and must never be served as a learner quiz. |

## Adjustment after the run

The prompt now explicitly prohibits answer_key_placeholder and concept_id in answer-key entries. It also asks for natural distractors based on taught relationships and rejects the awkward/unsupported patterns seen here. The raw failed response remains unchanged as evidence. No second live generation was run, so these changes are unverified with Claude.

## Limits and next check

One sample provides no reliability estimate. Next, generate a fresh sample through controller validation and its reserved repair path, then review content again. Focused retry generation and adversarial live testing still need evaluation. This run does not establish student learning improvement or mastery.


## Revised prompt through controller

Report: `results/2026-10-26T18-35-41-271377+00-00-assessment-controller.json`. This run used the revised prompt, a fixture Tutor with controller-generated IDs, the real Python/Node Assessment bridge, and an isolated in-memory SQLite database. It made one adapter invocation, which failed at the model-call stage after 18,156 ms. No output reached quiz validation. The safe error boundary does not preserve the provider failure category, so timeout, provider rejection, truncation, and transport failure cannot be distinguished from this report. Do not infer the cause or token charges from this result.

The controller returned failed status with no next step and persisted zero quizzes, questions, keys, attempts, or results. Learning retry count and technical repair count both remained zero; no repair call was made for the provider failure. No learner key leak or foreign-key violation was found. This verifies failure handling in this run, not revised-prompt correctness or live repair success. Next improvement: preserve a fixed safe failure code (without provider bodies or credentials) before another live run.
