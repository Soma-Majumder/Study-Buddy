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


## Simplified-prompt live evaluation

Report: `results/2026-10-26T18-51-34-801701+00-00-assessment-controller.json`. The 534-word prompt ran with the same 8,192-token allowance and 60-second request deadline. The one invocation took 33,028 ms and returned `output_truncated` / `max_tokens`. Capture ends during the answer-key rationale for position 7. The controller saved no quiz/questions/keys/attempts/results, recorded failed status, and consumed neither technical repair nor learning retry. No additional generation was attempted.

Partial inspection, not a full quiz score: the six complete visible rationales are single sentences under the 25-word target. This is a narrower improvement in visible brevity, not proof of lower total token usage or better questions. Q2 and Q8 test the saturation/packing/fluidity relationship, but most other items still ask recall. Wrong options such as cholesterol “dissolves the bilayer” or “removes all proteins” remain obvious unsupported distractors. Q9 avoids the previously overlapping cold-condition choices by using much weaker alternatives rather than demonstrating strong distractor quality.

The raw text also closes the root object before `answer_key`, so that captured prefix already contains a JSON structural defect in addition to being truncated. It was not manually repaired or forwarded. The transport correctly treats a max_tokens response as incomplete before JSON validation.

Conclusion: the simplified prompt did not resolve completion, and pedagogical improvement remains unestablished. Before more paid prompt iterations, record numeric provider usage (including thinking-token totals when supplied) and inspect whether the generation budget is being spent on reasoning versus returned quiz text. The captured response text excludes private reasoning and does not establish that allocation. Do not infer a provider configuration fix from this run alone.


## Numeric token-usage capture and live diagnostic run

Report: `results/2026-10-26T18-54-14-327963+00-00-assessment-controller.json`. The evaluation-only capture now retains allowlisted nonnegative integer input/output/cache counts and `output_tokens_details.thinking_tokens` when supplied. No private thinking text is stored. Reports attach per-generation usage, totals including repairs, and thinking/output share only when counts are present and consistent. Missing values stay unknown, not zero.

| Generation | Input tokens | Output tokens | Reported thinking tokens | Latency | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Initial | 3309 | 5100 | 3171 | 19627 ms | JSON parser rejected an extra trailing closing brace |
| One reserved repair | 3398 | 5442 | 3475 | 21402 ms | Validated and saved |
| Total | 6707 | 10542 | 6646 | 41029 ms across calls | One completed quiz |

Reported thinking/output share is approximately 63.0%. Each call remained below its 8192-token cap; the total spans two calls and must not be compared to a single-call cap. Output tokens not counted as thinking total 3896, but this subtraction is not presented as an independently measured visible-text token count. Provider-reported cache counts were zero. These counts are not a dollar-cost estimate.

This supports investigating reasoning configuration before further prompt-only or budget-only tuning. It does not establish the usage split in historical truncated runs, which lacked captured usage. No provider setting was changed based on an assumed reasoning API.

Controller checks passed: one quiz, ten questions, ten private keys, awaiting_answers status, one technical repair, zero learning retries, no attempts/results, no learner key leak, and no foreign-key error. Assistant content inspection still finds weak distractors (for example Q6 breaking the bilayer apart, Q7 driving lipids into water) and mostly recall. Completion and format success do not establish improved question quality.

Offline verification: three capture/aggregation tests and all 109 Python tests passed; existing unrelated unclosed SQLite connection warnings remain. The new tests cover truncation metadata, missing/invalid values, repair aggregation, and exclusion of provider headers, error bodies, and private reasoning.


## Explicit low-effort Assessment evaluation

Report: `results/2026-10-26T18-57-27-777787+00-00-assessment-controller.json`. Assessment now sends `output_config: {effort: low}`; thinking remains unset (adaptive default), max_tokens remains 8192, and request/bridge deadlines remain 60/70 seconds. Tutor still omits effort and retains its previous defaults.

Offline checks: 59 JavaScript tests and 12 bridge/capture tests passed. Tests verify explicit effort serialization, omission for default transport/Tutor, and rejection of invalid effort before network access. No retry policy changed.

| Metric | Previous default-effort run | This low-effort run |
| --- | ---: | ---: |
| Generations | 2 (one repair) | 1 (no repair) |
| Total input tokens | 6707 | 3305 |
| Total output tokens | 10542 | 4710 |
| Reported thinking tokens | 6646 | 2772 |
| Thinking share | 63.0% | 58.9% |
| Summed call latency | 41029 ms | 17838 ms |
| Controller outcome | awaiting_answers | awaiting_answers |

Totals compare whole evaluation runs, so the larger difference includes the avoided repair. Comparing first calls only: output tokens were 5100 before versus 4710 here, and thinking tokens 3171 versus 2772. This is one sample per setting, not a controlled reliability or causal performance claim. All controller gates passed: one quiz, ten questions/keys, no learner key leak, zero attempts/results, zero technical repairs and learning retries, and no foreign-key errors.

### Per-question assistant review

All ten keyed answers and rationales appear supported by the supplied fixture explanation. Rationales are single sentences, 13–19 words each. No previously observed cold-condition synonym overlap appears. However, the quiz is not quality-approved.

| Position | Primary demand | Quality finding |
| --- | --- | --- |
| 1 | Recall arrangement | Wrong options include cholesterol/sugars as parts of each phospholipid: category mismatch makes guessing easy. |
| 2 | Cause/effect | Correct packing relationship is clear, but sugar/cholesterol attachment alternatives are untaught and weak. |
| 3 | Comparison | Tests taught saturated/unsaturated difference; glycocalyx and leaflet-switching options are unrelated distractors. |
| 4 | Recall list | Stem asks broadly what affects fluidity without limiting to the explanation; learner may need to reject plausible unlisted factors. |
| 5 | Recall conditional role | Correct very-fluid buffering role; chemical conversion and leaflet-switching alternatives are weak. |
| 6 | Recall conditional role | No overlapping correct alternatives, but “freeze solid” and “hides heads from water” are obvious wrong options. |
| 7 | Recall model | Correct answer is the only long, detailed description; “sugar wall” is an easy category mismatch. |
| 8 | Recall movement rule | Preserves rare-without-enzymes qualifier; glycocalyx and identical-leaflet alternatives are unrelated. |
| 9 | Recall definition/role | Correct answer is much more detailed than alternatives and already gives the recognition role. |
| 10 | Recall role | Overlaps with Q9's recognition wording; does not establish a different understanding target. |

Only Q2 and Q3 clearly require the rubric's understanding tasks rather than sentence/role recall, below the six-item quality target. Completion improved in this sample, while pedagogical quality remains unresolved. Next: refine the quiz-generation approach using concrete good/bad question examples or a separate quality review step, keeping controller format checks and bounded operations distinct from semantic review. No extra generation was made.


## Prepared concrete question-design examples

Added two brief examples to the Assessment prompt: a double-bond/packing/movement cause-and-effect question and a two-condition cholesterol comparison. Wrong choices reverse the same taught relationships rather than introduce absurd facts or unrelated molecule categories. Keys and rationales are explicitly developer/model instruction examples, not learner-visible fields. The examples cannot supply new facts or override current concept/retry scope. No paid generation was made; future evaluation must check both genuine transfer and avoidance of duplicate questions.


## Concrete-example transfer evaluation

Report: `results/2026-10-26T19-00-59-686290+00-00-assessment-controller.json`. Same low effort, 8192-token budget, 60/70-second deadlines, fixture Tutor, and one-repair controller limit. Initial output failed schema due to an extra concept_id in an answer-key entry. The single reserved repair passed validation. One quiz, ten questions/keys, awaiting_answers, repair count one, learning retry zero, zero attempts/results, no key leak, and no foreign-key error.

Usage across two calls: 7967 input tokens, 9661 output tokens, 5533 reported thinking tokens (57.3%). Call latencies: 16476 ms and 20824 ms. Counts span two calls, not one per-call cap.

### Result: follows some patterns, but fails the no-copying and quality goals

Q5 repeats the example stem exactly: “Which pair matches cholesterol's roles in a very fluid membrane and in the cold, respectively?” Its choices reproduce the same two-condition combinations with different order and minor stiffening wording. This is example copying, not demonstrated transfer. Q3 likewise closely follows the example's four packing/movement combinations, with a different stem.

| Question | Demand and evidence | Finding |
| --- | --- | --- |
| 1 | Recall heads/tails arrangement | Parallel arrangement choices; useful format, still recall. |
| 2 | Compare saturated/unsaturated packing | Comparison is useful, but overlaps Q3; rationale adds “saturated tails lack double bonds,” which the supplied explanation does not explicitly state. |
| 3 | Double-bond packing/fluidity effect | Parallel relationship choices improve over unrelated molecules, but closely copy the example pattern and overlap Q2. “Most direct” effect is actually the kink before packing/fluidity. |
| 4 | Recall fluidity-factor list | Adds distracting untaught terms such as pore size and pH; asks broadly what affects fluidity instead of what the explanation lists. |
| 5 | Compare cholesterol conditions | Exact copied example stem; same choice combinations shuffled. |
| 6 | Correct cholesterol misconception | Option a (“increases fluidity only in the cold”) and the chosen buffering description can both seem defensible from the cold/very-fluid contrast. Correct answer is also much longer. Reject ambiguity. |
| 7 | Recall fluid mosaic model | Supported key; some useful contrasts, but correct answer remains a detailed definition. |
| 8 | Recall leaflet-switching rule | “Why” does not make this a mechanism question: correct choice restates the rule, while other choices are unsupported and extreme. |
| 9 | Recall leaflet composition | Several options concern different relationships; not a demanding comparison. |
| 10 | Recall glycocalyx role | Wrong roles are unrelated; correct choice is conspicuously longer and more detailed. |

Rationales are one sentence, 11–19 words each. Most keys match the explanation, but the ambiguity and added rationale detail above block quality approval. About four items target comparison/cause/correction (Q2, Q3, Q5, Q6), below the six-item target; two closely reuse examples and two pairs overlap in learning target. No student outcome or robust improvement is established.

Next design adjustment: replace lesson-specific question text with an abstract planning pattern, or use examples from another topic clearly excluded from factual input, then review grounding and transfer carefully. Do not weaken the rubric or silently accept copied/ambiguous questions just because schema validation succeeds. No third generation was made.


## Prepared abstract design pattern

Removed the two lesson-specific question examples after the model copied one verbatim. Their replacement is a five-step abstract design pattern: choose a distinct taught relationship, ask for an application, construct parallel plausible confusions, reject equivalent/narrower-true alternatives, and write a short explanation-grounded rationale. No additional factual material or question templates are provided. Existing privacy, exact JSON, scope, and controller stopping rules are unchanged. No paid generation was made, and effectiveness remains to be tested live.


## Abstract-pattern live evaluation: failed

Report: `results/2026-10-26T19-04-02-330163+00-00-assessment-controller.json`. Same low effort, 8192-token per-call budget, 60/70-second deadlines, fixture Tutor, and one-repair limit. Both captured responses were complete valid JSON, but each failed the existing schema due to an extra concept_id in an answer-key entry (first Q4; repair Q2). No field was removed automatically and neither output was accepted. The controller stopped after the reserved repair, saved zero quizzes/questions/keys/attempts/results, recorded failed status, and left learning retry zero. No key leak or foreign-key issue was found.

| Generation | Latency | Output tokens | Reported thinking tokens | Outcome |
| --- | ---: | ---: | ---: | --- |
| Initial | 34442 ms | 8179 | 6375 | Invalid extra answer-key field |
| Repair | 27066 ms | 6662 | 4793 | Same field defect, different entry |
| Total | 61508 ms | 14841 | 11168 | Failed, no quiz saved |

Total input tokens: 7767. Thinking share approximately 75.3%. The first call finished only 13 tokens below the configured cap but did not report truncation. Low effort did not consistently produce lower thinking usage across these samples; do not claim it guarantees speed or completion.

### Content inspection of rejected outputs only

The removed verbatim cholesterol comparison stem did not recur, but this alone does not show useful transfer: the repair largely returned to simple recall. The first response again offered overlapping cold-condition choices (“make the membrane more fluid” versus “prevent excessive stiffening”).

| Repair item | Finding |
| --- | --- |
| Q1 | Supported arrangement answer, but some choices are unrelated or not parallel locations. |
| Q2 | Recall definition, plus the forbidden concept_id inside its key. |
| Q3 | Useful saturated/unsaturated comparison; correct choice is longest. |
| Q4 | Recall fluidity definition with category-mismatch wrong choices. |
| Q5 | Recall list; asks for a pair while some choices are single factors with giveaway “only.” |
| Q6 | Correct cholesterol role, but “breaks the bilayer apart” is still a weak distractor. |
| Q7 | “Causing excessive stiffening” and “excessive stiffening” overlap as things cholesterol helps prevent. Reject ambiguity. |
| Q8 | Supported fluid mosaic answer, mostly recall. |
| Q9 | “Why” only restates the taught movement rule; rationale incorrectly says leaflets rarely switch, instead of lipids switching leaflets. |
| Q10 | Recall glycocalyx role; correct choice is much more detailed than alternatives. |

The abstract design instructions did not meet the six-understanding-item target, remove ambiguity, or establish quality improvement. No accepted quiz exists to recommend to learners. Next prioritize the recurring output-contract defect before more prompt-only iteration; separately design a reliable content review/revision approach for ambiguous and unsupported items. No third paid call was made.


## Output-format fix: API-constrained schema, live success

No pedagogical prompt tuning was done in this step. Added a repo-derived schema projection to Claude output_config.format and merged it with effort. Extra keys are prohibited; the full original Python schema/semantic validator remains authoritative. Local insufficient-content failures are preserved as an explicit schema branch.

API compatibility: initial top-level quiz/error union attempts returned HTTP 400; three controller reports at 19:07:33, 19:08:25, and 19:09:34 record safe status and zero saved records. Two additional diagnostic rejection probes inspected only allowlisted schema-error indicators/wording, without recording raw error bodies. The API rejected top-level anyOf, so the final schema uses a strict root object with required result containing the union. The adapter unwraps result before controller validation.

Successful report: `results/2026-10-26T19-10-34-267200+00-00-assessment-controller.json`. One call, 27256 ms, zero validation errors, zero repairs. Saved one quiz, ten questions, ten private keys; session awaiting_answers; zero attempts/results and learning retries; no learner key leak or foreign-key errors. Usage: 5213 input tokens, 6284 output tokens, 4459 reported thinking tokens (~71.0% share). No output budget or effort change was made.

All 64 JavaScript and 109 Python tests passed; preexisting unclosed SQLite connection warnings remain. New tests verify grammar rejection of the recurring extra fields, success/error branches, API request merging, unsupported-bound projection, and original-schema enforcement of ten items. Existing fake-provider bridge tests were updated to return the transport envelope without bypassing controller validation.

This is a verified format fix on one live sample, not a reliability estimate or semantic-quality approval. Some constraints are not expressible in the provider grammar and still require local validation. No content-quality claim is made for the newly accepted quiz; review ambiguity, distractors, and grounding separately.


## Completed quality review of the schema-valid quiz

See `assessment-quality-review-2026-10-26.md` and the saved report's quality_review object for per-question evidence and rubric judgments. All ten keys/rationales appear supported; no clear answer overlap was found in this sample. Coverage includes seven concepts, with 9–13-word rationales. Only Q2/Q3 clearly test understanding; other items mostly require recall. Weak distractors and some unfair cues mean needs_revision. This is manual report metadata, not a runtime quality gate or an independent scientific/educator review. No paid call or prompt change was made.
