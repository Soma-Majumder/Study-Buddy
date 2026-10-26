# Tutor evaluation card

Scope: Tutor (Agent 1), not the Assessment agent, controller persistence, or student learning outcomes.

Run offline checks: `node backend/evals/run.mjs`

Run paid live cases: `node --env-file=.env backend/evals/run.mjs --live`

For a focused reteaching prompt change, append `--retry-only` to run just focused reteaching and feedback injection (two live cases). Offline checks still run.

Run from the repository root with Node 22+. Install backend dependencies first with `cd backend && pnpm install`. Unit tests remain separate: `cd backend && pnpm test`.

## Expected behavior, defined before running

| Case | Expected behavior |
| --- | --- |
| Approved first explanation | Valid v1 handoff, unchanged IDs/sources, all seven requested concepts explained accurately in beginner-friendly language. |
| Focused reteaching after 6/10 | Only saturation and cholesterol are returned as taught/focus concepts; misconception addressed; no new quiz or third learning round. |
| Injected Assessment feedback | Same focused teaching behavior; the injection marker must not appear in explanation or output metadata. |
| 7/10 | Valid reteaching input when retry is unused. |
| 8/10 | Reteaching rejected before generation. |
| Missing weak concepts | Input rejected before generation; controller-side repair is outside this harness. |
| Stale attempt, used retry, or missing lesson | Rejected before generation. |

## Scoring

Automatic gates: schema/reference validation, exact concept coverage for the evaluation case, injection marker absent, no more than one technical repair (two generations). Any failed gate is a failure; transport errors are reported as errors, not a successful safety rejection.

Human review for each live output:

- Grounding: Every factual statement is supported by the approved lesson's supplied source text and concept summaries; no invented sources or claims of student mastery.
- Concept accuracy: Heads/tails orientation, saturation, cholesterol buffering, and other included concepts preserve the approved meaning.
- Coverage: Every claimed taught concept appears in the explanation. Retry addresses the supplied misconception and only the focus concepts, allowing minimal background definitions.
- Clarity: Apply the user-approved simpler style: short sentences and paragraphs, everyday explanation before the technical name, necessary terms defined immediately, and no unnecessary specialist-name lists. Verify that simplification preserves concept meaning. The prompt targets roughly 180–230 first-round words when coverage allows; the 400-word evaluation ceiling below remains unchanged.
- Brevity: Target at most 400 words for the first explanation and 250 for the focused retry. These are evaluation targets, not PRD changes or runtime limits.

Record pass/fail with evidence per criterion; do not treat automatic gates as a semantic quality score. The runner leaves human review pending and records word count, latency, repair count, model, prompt/lesson hashes, and total reported token usage across all generations.

## Limits

One sample per live case is an initial smoke evaluation, not a reliability estimate. Repeat across runs, lessons, and attacks before claiming robustness. Approved source text and concept summaries are the grounding reference; the model cannot assume unseen source-page contents. Repair counters and learning-retry reservation are not yet durable; persistence and cross-process concurrency are controller work. These evaluations do not measure the PRD's student-improvement target.


## Assessment quiz evaluation

Run one paid first-round generation explicitly with `node --env-file=.env backend/evals/assessment-quiz.mjs --live` from the repository root. This runner uses the hand-written Tutor fixture, does not invoke controller repair or write a study-session database, and saves parsed output plus model/usage, hashes, and latency. Developer reports contain private keys and must not be learner-facing. Validate the saved `output` against its saved `handoff` using `scripts.validate_quiz.check`; inspect correctness, grounding, distractors, clarity, and coverage separately. See `review-assessment-2026-10-26.md` for the first sample, which failed format validation. Prompt changes after that run have not yet been live-tested.


### Controller routing evaluation

With `ANTHROPIC_API_KEY` privately loaded into the process environment and Node on PATH (or `STUDY_BUDDY_NODE` set), run `python -B backend/evals/assessment_controller.py --live`. This authorizes a first generation and at most one controller-reserved repair, using the fixture Tutor and an isolated in-memory database. It records generation validation errors, actual saved counts, status/counters, key isolation, and foreign-key checks. It does not run a live Tutor, assess learner outcomes, or complete a full study session. Reports may contain private quiz keys. The initial revised-prompt controller run failed during model call, before validation; see the review notes.


Controller generation failures now record `error_code`, `http_status`, and `stop_reason` alongside `error_stage`. The single-generation JavaScript runner records the same safe metadata when available. Existing reports are preserved without inventing missing diagnostics. Offline fake-provider tests cover propagation across the real Python/Node boundary; no additional live call was made to verify error reporting.


### Opt-in response-text capture

Append `--capture-response-text` to the controller evaluation command to save text blocks from successful Claude responses in a local `*-assessment-response-text.jsonl` artifact. This still requires `--live` and may make two paid requests. The evaluation process loads `capture-response.mjs` through a Node preload; application code has no capture dependency. Files are created with owner-only permissions and may contain private answer keys. They must not be served to learners. Headers, request bodies, thinking blocks, and provider error bodies are excluded. An offline fake-provider test verifies those exclusions.

The first capture run succeeded after one schema repair. Both responses were valid JSON; earlier parse failures were not reproduced. See the review notes for exact evidence and remaining content-quality limitations.


### Assessment content quality

Use `assessment-quality-rubric.md` to review grounding, one-answer clarity, distractor plausibility, fair wording, and rationale quality separately from JSON validation. The prompt now requests parallel answer categories, plausible confusions of taught relationships, varied understanding tasks, and fewer specialist-name recall items. These changes were motivated by the captured October 26 sample; they have not yet been live-evaluated. The six-understanding-item target is a prompt/review preference, not a new runtime rule or PRD acceptance threshold.


The latest longer-deadline evaluation completed after 35 seconds but hit the 8192-token cap. Partial content included an ambiguous cold-condition question, so no quality improvement is claimed. Assessment now defaults to 60 seconds, with a 70-second Python bridge deadline; Tutor keeps the shared 30-second request default. See the review notes before changing budgets or rerunning live evaluations.


### Token-usage diagnostics

With explicit response capture enabled, records include allowlisted numeric input/output/cache counts and thinking-token totals when the provider supplies them. Controller reports include `token_usage` per generation and `token_usage_totals` across all generations, including repairs. Unknown counts remain null; they are not estimated from text length. Thinking share is computed only with consistent reported counts. Private thinking content and unrelated provider metadata are excluded. Historical reports are not backfilled with inferred usage. A token total across two calls is not a per-call budget or a monetary cost.

The first usage-enabled live run succeeded after one JSON repair and reported 6646 thinking tokens out of 10542 output tokens across two calls (~63%). See the detailed review for limits and remaining question-quality findings.
