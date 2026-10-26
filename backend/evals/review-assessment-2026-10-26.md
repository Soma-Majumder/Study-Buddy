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


## Controller rerun with safe diagnostics

Report: `results/2026-10-26T18-38-53-246061+00-00-assessment-controller.json`. One live adapter invocation took 18,201 ms and failed with `error_code: output_truncated`, `stop_reason: max_tokens`. This run confirms the provider reached the shared transport's 4,096-token output budget before returning a complete response. The diagnostic does not retrospectively prove the earlier run had the same cause.

The controller recorded failed session status, zero quizzes/questions/keys/attempts/results, learning retry count zero, and technical repair count zero. No repair call was made for this provider failure. Key isolation and foreign-key checks passed. Prompt quality and the live validation/repair path remain unverified because no complete output reached validation. Next implementation step: make the output budget configurable and give Assessment a larger budget without changing Tutor's default; validate the configuration offline before another explicitly authorized live run.


## Increased Assessment output budget: 8,192 tokens

Report: `results/2026-10-26T18-40-19-056969+00-00-assessment-controller.json`. The shared transport now accepts a validated positive integer `maxTokens`; Tutor retains the 4,096 default, and Assessment supplies 8,192 by default. Offline tests confirm both settings and reject invalid budgets before network access.

This live controller run made two calls (20,256 ms and 21,060 ms). Neither returned a truncation error. Both completed transport but failed JSON parsing in the adapter, producing `output: null`; the validator reported “None is not of type object.” The controller reserved exactly one technical repair and failed after the second invalid generation. It saved no quiz, questions, keys, attempt, or result. Learning retry stayed zero, learner keys were not exposed, and foreign-key checks passed.

This confirms the live repair limit and rejection of non-JSON output. It does not establish quiz quality or sustained resolution of truncation. Raw non-JSON text was discarded by design, so the exact formatting defect is unknown; do not assume Markdown fences or any other cause. Next diagnostic step: capture model response text only in an explicit backend evaluation artifact (never a learner response or normal error), then inspect why parsing fails. No third live call was made.


## Explicit response-text capture: controller succeeds after one repair

Report: `results/2026-10-26T18-42-45-856555+00-00-assessment-controller.json`. Raw text: `results/2026-10-26T18-42-45-856555+00-00-assessment-response-text.jsonl`. Capture was explicitly enabled for this evaluation through a Node preload; normal application code does not load it. It records only successful response text blocks and an allowlisted stop reason, excluding request data/headers, thinking blocks, and provider error bodies. Raw text may contain quiz keys and is a backend developer artifact.

Both captured responses were valid JSON; the earlier JSON parsing failure was not reproduced, and its exact formatting cause remains unknown. The first output failed schema validation because an answer-key entry contained forbidden concept_id. The controller reserved one technical repair before the second call; the corrected output passed the existing quiz validator. Calls took 21,177 ms and 13,724 ms.

The isolated database contained exactly one quiz, ten questions, and ten private keys, with no attempts or results. Session status was awaiting_answers, technical repair count one, learning retry count zero, no foreign-key errors, and no keys in the learner response. This live sample demonstrates successful validation/repair/persistence, not robust generation across lessons or runs.

Assistant content inspection: all ten keyed answers and rationales appear supported by the fixture explanation; all seven taught concepts are covered. Several distractors remain easy category mismatches (for example sugars as flipping enzymes), and question 7 emphasizes specialist enzyme names rather than understanding. Do not equate schema validity with pedagogical quality. No third generation was made.


## Question-quality prompt evaluation: inconclusive due to timeout

Report: `results/2026-10-26T18-46-52-537794+00-00-assessment-controller.json`. This run used the new question-quality instructions (prompt hash saved in the report). One live adapter invocation failed with `request_timeout` after 30,152 ms. The opt-in response capture file is empty. No response text reached the adapter or validator, so none of the rubric's content criteria can be scored and no improvement claim is supported.

The controller saved zero quizzes, questions, keys, attempts, or results; session status is failed, technical repair count zero, learning retry count zero, and no learner key leak or foreign-key issue was found. No repair or further live call was made.

The next configuration change should give Assessment a longer request deadline to match its larger 8,192-token response budget, with a Python subprocess deadline longer than the request deadline. Keep Tutor's existing deadlines and all repair/learning retry limits unchanged. Test timeout behavior offline before another live quality evaluation. A longer timeout is not itself evidence of better questions.


## Quality rerun with Assessment-specific longer deadlines

Report: `results/2026-10-26T18-48-33-478279+00-00-assessment-controller.json`. The request deadline was extended to 60 seconds, with a 70-second Python subprocess deadline; Tutor deadlines and retry budgets remain unchanged. Offline checks: 57 JavaScript tests and nine Assessment bridge tests passed, including deadline/cancellation checks.

The one live invocation ran 35,013 ms and returned `output_truncated` / `max_tokens` at the 8,192-token allowance. Capture contains all ten questions and a partial answer key ending during position 9. The controller did not attempt JSON validation or repair after this transport failure; it saved zero quiz records or scores, recorded failed status, and left technical/learning retry counters at zero. The capture is incomplete evidence and was not repaired or accepted manually.

### Partial content inspection only — no accepted quiz to score

- Q5 is ambiguous: “prevents the membrane from becoming excessively stiff” and “increases movement so the membrane loosens” can both describe the cold-condition buffering effect. Its partial key chooses the former while incorrectly calling the latter a reversal. This fails the one-defensible-answer criterion.
- Q2 explicitly tests the taught double-bond → kink → packing → fluidity relationship, which is a useful understanding item. Q10 asks why tails point inward, but overlaps with Q1's bilayer arrangement.
- Q3, Q6, Q7, Q8, and Q9 still lean heavily on recalling a sentence or a named role. Q8's wrong options remain implausible and use giveaway absolutes such as “every” and “always.”
- Some rationales now contrast a mistaken idea with the correct relationship, but unsupported statements remain: the Q8 rationale turns “rare without enzymes” into “enzymes make switching possible,” and the Q7 rationale asserts sugars occur only on the outside beyond the supplied sentence.

The longer timeout resolved this run's deadline bottleneck but did not produce a complete, learner-ready quiz or establish improved quality. The next change should reduce generation verbosity and simplify the instructions while explicitly preventing overlapping cold-condition choices; do not blindly increase both budgets again. A fresh complete output and per-question review are needed. No further paid call was made.


## Prepared next prompt: shorter instructions and rationales

The prompt was rewritten to remove repeated planning/self-check guidance while preserving data/instruction separation, explanation-only grounding, copied IDs, exact output fields, one-answer quality, retry focus, private keys, and controller-owned stopping/repairs. Rationales now target one sentence and at most 25 words. Equivalent answer effects are explicitly prohibited, and qualifiers such as rare versus impossible must be preserved. This may reduce response length, but no reduction in reasoning tokens, latency, or failures is claimed without another live sample. No paid call was made for this edit.
