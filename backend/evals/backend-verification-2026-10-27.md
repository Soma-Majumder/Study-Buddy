# Backend verification — October 27, 2026

## Reviewer policy clarification

Repeated targets and fewer than six understanding questions are warnings under the existing policy. Reviewer must not turn a known style shortfall into speculative uncertainty about publication. Genuine unresolved grounding, ambiguity, rationale, coverage or diversity judgments still block. Controller code, schemas, call budgets and publication checks are unchanged. A regression test combines repetition warnings with genuine grounding uncertainty and confirms the latter still blocks.

## What was verified

| Test | Evidence and outcome |
| --- | --- |
| Fresh live Tutor → Assessment → Reviewer | Valid ten-question quiz; one revision exhausted; final review blocked unsupported warm/very-fluid substitution. No retry quiz or summary fabricated. |
| Corrected saved first quiz, clarified Reviewer prompt | Pass; five understanding questions, insufficient-understanding and repeated-target warnings. One paid Reviewer call; no generation. |
| Saved first quiz → live feedback → live reteaching → live retry generation/review | First score 7/10 and validated feedback/reteaching; retry blocked after one revision. Summary correctly not saved. Six paid adapter invocations. |
| Manually corrected saved retry | Pass with eight understanding questions and repeated-target warning. One paid Reviewer call; exact snapshot hash below. |
| Saved approved first/retry snapshots → grading → live retry feedback → final summary | Scores [7,10], exactly one learning retry, two quizzes/attempts/results, one saved summary, completed session, zero foreign-key violations. One paid feedback call, no generation or Reviewer calls. Summary verified after reopening the isolated database and repeated completion returned the same summary. |

Final first candidate: `backend/evals/revisions/2026-10-27-manual-options-fix.json`.
Final retry candidate: `backend/evals/revisions/2026-10-27-manual-retry-q1-fix.json`.
Both are assistant-authored evaluation references with private answer keys, never learner payloads. They are not production defaults, automatic agent revisions or gold-standard educational judgments.

Approved first hash: `8612d83118f18a516ec63b1259ee94152e66ae7c9d3b993933bb5f821f4c4640`.
Approved retry hash: `e212348353ef73a27eaa555c0530ec92b5038b9af4c1a42826be663de3c02b13`.

The completion test replayed saved first feedback and reteaching through normal controller functions into a new isolated database, rebinding session identifiers and validating reports against the rebound snapshots. It did not revive the failed earlier session or override its verdict. Only retry feedback was live. These are staged checks, not one uninterrupted all-live success.

## Offline verification and limits

The earlier full local checkout passed 505 Python and 95 JavaScript tests before the clarification regression was added (the Python total includes local learner-preview tests). The clarification passed 16 validator tests and 15 Reviewer adapter/input tests. Counts do not imply a fresh run on a different merged revision; see PR update for latest focused results.

Fresh automatic first/retry generation reliability remains unproven: repeated live checks found new content issues. Successful manually corrected snapshots establish the controller completion path and live feedback compatibility, not general agent readiness. Final manual edits do not train or change the generation prompts. Keep genuine uncertainty blocking and the single revision limit intact.

Scope: no new agents, calls, schemas, runtime features or frontend changes. Raw private run logs, local databases, credentials and learner-preview assets are excluded. Structured aggregate evidence is in `backend/evals/backend-verification-2026-10-27.json`.

## Final generation-prompt acceptance test — paid tuning stopped

The generation prompt adds one internal option check for parallel option detail, distractors grounded in taught relationships and exact condition/qualifier preservation. Runtime, schema, budgets and publication rules are unchanged.

One fresh live Tutor → Assessment → Reviewer test with these instructions produced ten structurally valid, in-scope questions. Initial review requested revision for Q4's answer-length clue. After the single content revision, final review still requested revision: Q3 had unrelated/easily eliminated distractors and Q4 retained length/wording cues. Controller returned `content_revision_exhausted`, saved no learner quiz, attempt or result, and did not fabricate success. All five provider calls completed without truncation in 96.44 seconds.

Offline verification before this run: 76 targeted Python tests and all 95 JavaScript tests passed. These prove compatibility, not semantic question quality. Aggregate run evidence, usage and prompt hashes are in `backend/evals/generation-acceptance-2026-10-27.json`. Raw model content, private databases and credentials are excluded.

**Remaining limitation:** fresh quiz generation and its bounded revision have not demonstrated reliable content-review acceptance. The earlier saved-candidate passes and saved-summary completion are valid staged evidence, not proof of an uninterrupted all-live success. Do not describe the agents as fully ready or weaken the quality gate to obtain a pass.

**Stop decision:** paid prompt tuning is stopped at the user's request. No additional paid evaluations, model/budget changes, review loops or agents are part of this follow-up. Any future paid run requires renewed user authorization. Learner-interface work remains separate.
